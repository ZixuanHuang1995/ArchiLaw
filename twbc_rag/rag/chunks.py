"""Article parent chunks with clause children.

Deleted articles (the whole text is 「（刪除）」) are left out of the index.
They remain in Postgres so citation resolution during ingestion still works.
Box-drawing tables are flagged, and a full-text PDF attachment is linked
when the MOJ page said the text cannot represent the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from twbc_rag.catalog import load_catalog
from twbc_rag.db.models import Article, Clause, CrossRef, FigureTable, Law, LawVersion

_DELETED = {"（刪除）", "（刪除）。"}


@dataclass
class ClauseChild:
    clause_id: int
    clause_path: str
    text: str
    is_proviso: bool


@dataclass
class Attachment:
    file_name: str
    source_url: str
    media_type: str
    is_fulltext_pdf: bool


@dataclass
class Chunk:
    chunk_id: str
    pcode: str
    law_name: str
    aliases: list[str]
    heading_path: str
    article_no: str
    article_no_main: int
    article_no_sub: int
    article_id: int
    version_id: int
    modified_date: str
    effective_date: str | None
    effective_note: str
    official_url: str
    full_text: str
    clauses: list[ClauseChild]
    has_proviso: bool
    has_table: bool
    attachments: list[Attachment]
    out_chunk_ids: list[str] = field(default_factory=list)

    def citation_label(self, clause_path: str | None = None) -> str:
        label = f"{self.law_name} {self.article_no}"
        if clause_path:
            parts = clause_path.split("/")
            if parts and parts[0].isdigit():
                label += f"第{parts[0]}項"
            if len(parts) > 1 and parts[1].isdigit():
                label += f"第{parts[1]}款"
        return label


def is_deleted_article(text: str) -> bool:
    return text.strip() in _DELETED


def chunk_id_for(pcode: str, main: int, sub: int) -> str:
    if sub:
        return f"{pcode}:{main}-{sub}"
    return f"{pcode}:{main}"


def load_chunks(session: Session) -> list[Chunk]:
    catalog = load_catalog()
    laws = {law.pcode: law for law in session.scalars(select(Law)).all()}
    versions = list(session.scalars(select(LawVersion)).all())
    latest: dict[str, LawVersion] = {}
    for version in versions:
        current = latest.get(version.pcode)
        if current is None or (version.modified_date, version.version_id) > (
            current.modified_date,
            current.version_id,
        ):
            latest[version.pcode] = version
    if not latest:
        return []

    version_ids = [version.version_id for version in latest.values()]
    articles = list(
        session.scalars(
            select(Article).where(Article.version_id.in_(version_ids)).order_by(Article.seq)
        ).all()
    )
    article_ids = [article.article_id for article in articles]
    if not article_ids:
        return []
    clauses = list(
        session.scalars(
            select(Clause).where(Clause.article_id.in_(article_ids)).order_by(Clause.seq)
        ).all()
    )
    assets = list(
        session.scalars(select(FigureTable).where(FigureTable.version_id.in_(version_ids))).all()
    )
    clause_ids = [clause.clause_id for clause in clauses]
    refs = []
    if clause_ids:
        refs = list(
            session.scalars(
                select(CrossRef).where(
                    CrossRef.from_clause_id.in_(clause_ids),
                    CrossRef.to_article_id.is_not(None),
                )
            ).all()
        )

    clauses_by_article: dict[int, list[Clause]] = {}
    for clause in clauses:
        clauses_by_article.setdefault(clause.article_id, []).append(clause)
    assets_by_version: dict[int, list[FigureTable]] = {}
    for asset in assets:
        assets_by_version.setdefault(asset.version_id, []).append(asset)
    refs_by_clause: dict[int, list[int]] = {}
    for ref in refs:
        if ref.to_article_id is not None:
            refs_by_clause.setdefault(ref.from_clause_id, []).append(ref.to_article_id)

    chunks: list[Chunk] = []
    id_by_article: dict[int, str] = {}
    for article in articles:
        if is_deleted_article(article.full_text):
            continue
        version = next(item for item in latest.values() if item.version_id == article.version_id)
        law = laws[version.pcode]
        cfg = catalog.by_pcode(law.pcode)
        aliases = list(cfg.aliases) if cfg else [law.name]
        if law.name not in aliases:
            aliases.append(law.name)
        children = [
            ClauseChild(
                clause_id=clause.clause_id,
                clause_path=clause.clause_path,
                text=clause.text,
                is_proviso=clause.is_proviso,
            )
            for clause in clauses_by_article.get(article.article_id, [])
        ]
        attachments = [
            _attachment(asset) for asset in assets_by_version.get(version.version_id, [])
        ]
        # Prefer files that name this article; also keep law-level full-text PDFs
        # whose filename contains this article number.
        mine = [item for item in attachments if _belongs(item, article.article_no)]
        chunk_id = chunk_id_for(law.pcode, article.article_no_main, article.article_no_sub)
        id_by_article[article.article_id] = chunk_id
        chunks.append(
            Chunk(
                chunk_id=chunk_id,
                pcode=law.pcode,
                law_name=law.name,
                aliases=aliases,
                heading_path=article.heading_path,
                article_no=article.article_no,
                article_no_main=article.article_no_main,
                article_no_sub=article.article_no_sub,
                article_id=article.article_id,
                version_id=version.version_id,
                modified_date=version.modified_date.isoformat(),
                effective_date=version.effective_date.isoformat()
                if version.effective_date
                else None,
                effective_note=version.effective_note or "",
                official_url=law.official_url,
                full_text=article.full_text,
                clauses=children,
                has_proviso=any(child.is_proviso for child in children),
                has_table=("┌" in article.full_text or "│" in article.full_text),
                attachments=mine,
            )
        )

    for chunk in chunks:
        targets: list[str] = []
        for child in chunk.clauses:
            for target_article_id in refs_by_clause.get(child.clause_id, []):
                target = id_by_article.get(target_article_id)
                if target and target != chunk.chunk_id and target not in targets:
                    targets.append(target)
        chunk.out_chunk_ids = targets
    return chunks


def _attachment(asset: FigureTable) -> Attachment:
    name = asset.file_name or ""
    caption = asset.caption_or_ocr or ""
    fulltext = "完整條文" in name or "無法完整呈現" in caption
    return Attachment(
        file_name=name,
        source_url=asset.source_url,
        media_type=asset.media_type,
        is_fulltext_pdf=fulltext,
    )


def _belongs(item: Attachment, article_no: str) -> bool:
    compact = article_no.replace(" ", "")
    name = (item.file_name or "").replace(" ", "")
    return bool(compact) and compact in name
