"""Counts read back from Postgres so the CLI reports what was stored."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from twbc_rag.db.models import Article, Clause, CrossRef, FigureTable, Law, LawVersion
from twbc_rag.ingest.report import LawStats

_DELETED = {"（刪除）", "（刪除）。"}


def collect_stats(session: Session) -> list[LawStats]:
    laws = session.scalars(select(Law).order_by(Law.pcode)).all()
    rows: list[LawStats] = []
    for law in laws:
        version = session.scalar(
            select(LawVersion)
            .where(LawVersion.pcode == law.pcode)
            .order_by(LawVersion.modified_date.desc(), LawVersion.version_id.desc())
        )
        if version is None:
            continue
        articles = session.scalars(
            select(Article).where(Article.version_id == version.version_id).order_by(Article.seq)
        ).all()
        rows.append(
            LawStats(
                pcode=law.pcode,
                name=law.name,
                modified_date=version.modified_date.isoformat(),
                effective_date=version.effective_date.isoformat()
                if version.effective_date
                else None,
                effective_note=version.effective_note or "",
                source=version.source,
                articles=len(articles),
                clauses=_count_clauses(session, version.version_id, provisos_only=False),
                provisos=_count_clauses(session, version.version_id, provisos_only=True),
                cross_refs_resolved=_count_refs(session, version.version_id, resolved=True),
                cross_refs_unresolved=_count_refs(session, version.version_id, resolved=False),
                attachments=_count_assets(session, version.version_id),
                deleted_articles=sum(
                    1 for article in articles if article.full_text.strip() in _DELETED
                ),
                table_articles=sum(
                    1
                    for article in articles
                    if "┌" in article.full_text or "│" in article.full_text
                ),
                layout_warnings=_count_layout_warnings(session, version.version_id),
            )
        )
    return rows


def format_stats(rows: list[LawStats]) -> str:
    if not rows:
        return "No laws stored."
    blocks: list[str] = []
    totals = LawStats(
        pcode="TOTAL",
        name=f"{len(rows)} laws",
        modified_date=None,
        effective_date=None,
        effective_note="",
        source="",
        articles=sum(row.articles for row in rows),
        clauses=sum(row.clauses for row in rows),
        provisos=sum(row.provisos for row in rows),
        cross_refs_resolved=sum(row.cross_refs_resolved for row in rows),
        cross_refs_unresolved=sum(row.cross_refs_unresolved for row in rows),
        attachments=sum(row.attachments for row in rows),
        deleted_articles=sum(row.deleted_articles for row in rows),
        table_articles=sum(row.table_articles for row in rows),
        layout_warnings=sum(row.layout_warnings for row in rows),
    )
    for row in [*rows, totals]:
        effective = row.effective_date or "(none)"
        modified = row.modified_date or "-"
        source = row.source or "-"
        blocks.append(
            "\n".join(
                [
                    f"{row.pcode}  {row.name}",
                    f"  modified {modified}  effective {effective}  source {source}",
                    f"  articles {row.articles}  clauses {row.clauses}  provisos {row.provisos}",
                    (
                        f"  cross_refs resolved {row.cross_refs_resolved}"
                        f"  unresolved {row.cross_refs_unresolved}"
                    ),
                    (
                        f"  attachments {row.attachments}  deleted_articles {row.deleted_articles}"
                        f"  table_articles {row.table_articles}"
                        f"  layout_warnings {row.layout_warnings}"
                    ),
                ]
            )
        )
    return "\n\n".join(blocks)


def _count_clauses(session: Session, version_id: int, *, provisos_only: bool) -> int:
    stmt = (
        select(func.count(Clause.clause_id))
        .join(Article, Article.article_id == Clause.article_id)
        .where(Article.version_id == version_id)
    )
    if provisos_only:
        stmt = stmt.where(Clause.is_proviso.is_(True))
    return int(session.scalar(stmt) or 0)


def _count_refs(session: Session, version_id: int, *, resolved: bool) -> int:
    stmt = (
        select(func.count(CrossRef.cross_ref_id))
        .join(Clause, Clause.clause_id == CrossRef.from_clause_id)
        .join(Article, Article.article_id == Clause.article_id)
        .where(Article.version_id == version_id)
    )
    if resolved:
        stmt = stmt.where(CrossRef.to_article_id.is_not(None))
    else:
        stmt = stmt.where(CrossRef.to_article_id.is_(None))
    return int(session.scalar(stmt) or 0)


def _count_assets(session: Session, version_id: int) -> int:
    stmt = select(func.count(FigureTable.asset_id)).where(FigureTable.version_id == version_id)
    return int(session.scalar(stmt) or 0)


def _count_layout_warnings(session: Session, version_id: int) -> int:
    captions = session.scalars(
        select(FigureTable.caption_or_ocr).where(FigureTable.version_id == version_id)
    ).all()
    return sum(1 for caption in captions if caption and "無法完整呈現" in caption)
