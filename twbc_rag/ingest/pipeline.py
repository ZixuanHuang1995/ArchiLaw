"""Idempotent, version-aware load of configured pcodes into Postgres.

Re-running with the same ``LawModifiedDate`` does not write a new version.
A changed modified date inserts another ``law_version`` and leaves the old
one in place. ``--force`` deletes that date's version and parses it again.

Cross-references are rebuilt for every stored version whenever this run
inserts or forces at least one version, so a newly added law can satisfy
cites that were unresolved before. Targets always point at the latest
version of the cited law.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from twbc_rag.catalog import Catalog, LawConfig, fallback_config, load_catalog
from twbc_rag.db.models import Article, Clause, CrossRef, FigureTable, Law, LawVersion
from twbc_rag.db.session import session_scope
from twbc_rag.export.jsonl import export_jsonl
from twbc_rag.ingest.huggingface import load_hf_laws
from twbc_rag.ingest.moj import MOJ_LAW_URL, MOJ_ORDER_URL, fetch_moj_payload, parse_moj_payload
from twbc_rag.ingest.pages import (
    PageAsset,
    canonical_file_url,
    fetch_law_page,
    file_id_of,
    media_type_of,
)
from twbc_rag.ingest.report import IngestReport, LawRunStatus
from twbc_rag.ingest.stats import collect_stats
from twbc_rag.ingest.types import RawLaw, SourceError
from twbc_rag.parse.clauses import ParsedClause, split_clauses
from twbc_rag.parse.crossref import (
    ClauseView,
    LawResolveContext,
    extract_refs_for_article,
    find_shorthand,
)
from twbc_rag.parse.crossref import (
    parse_article_label as label_to_key,
)
from twbc_rag.parse.headings import format_heading_path, update_heading_stack
from twbc_rag.paths import DEFAULT_JSONL_DIR, DEFAULT_RAW_DIR

logger = logging.getLogger(__name__)

_ARTICLE_IN_NAME = re.compile(r"第\s*(\d+)\s*(?:-\s*(\d+))?\s*條")
_TRAILING_NOTE = re.compile(r"（[^）]*）\s*$")


@dataclass
class _PreparedArticle:
    article_no: str
    main: int
    sub: int
    heading_path: str
    full_text: str
    clauses: list[ParsedClause]


@dataclass
class _MergedAsset:
    source_url: str
    file_name: str
    article_main: int | None
    article_sub: int | None
    warning: str | None
    source_kind: str


def ingest_configured(
    *,
    pcodes: list[str] | None = None,
    config_path: Path | None = None,
    refresh: bool = False,
    force: bool = False,
    fetch_pages: bool = True,
    jsonl_dir: Path | None = DEFAULT_JSONL_DIR,
    database_url: str | None = None,
    raw_dir: Path | None = None,
) -> IngestReport:
    catalog = load_catalog(config_path)
    selected = [pcode.strip().upper() for pcode in pcodes] if pcodes else list(catalog.pcodes)
    laws, missing, notes = load_corpus(
        selected, refresh=refresh, raw_dir=raw_dir or DEFAULT_RAW_DIR
    )
    pages: dict[str, list[PageAsset]] = {}
    if fetch_pages:
        for law in laws:
            pages[law.pcode] = fetch_law_page(law.pcode)
    with session_scope(database_url) as session:
        report = ingest_raw_laws(session, laws, catalog, page_assets=pages, force=force)
        report.missing_pcodes = missing
        report.source_notes = notes
        if jsonl_dir is not None:
            export_jsonl(session, jsonl_dir)
    if jsonl_dir is not None:
        _write_report(jsonl_dir / "ingest_report.json", report)
    return report


def load_corpus(
    pcodes: list[str],
    *,
    refresh: bool,
    raw_dir: Path,
) -> tuple[list[RawLaw], list[str], list[str]]:
    """MOJ zip first. Hugging Face fills any pcode the zip did not return."""
    notes: list[str] = []
    remaining = set(pcodes)
    loaded: dict[str, RawLaw] = {}
    raw_dir.mkdir(parents=True, exist_ok=True)
    for label, url, filename in (
        ("law", MOJ_LAW_URL, "ch_law.zip"),
        ("order", MOJ_ORDER_URL, "ch_order.zip"),
    ):
        if not remaining:
            break
        try:
            payload = fetch_moj_payload(url, raw_dir / filename, refresh=refresh)
        except SourceError as exc:
            notes.append(f"MOJ {label} zip failed: {exc}")
            logger.warning("MOJ %s zip failed: %s", label, exc)
            continue
        matched = parse_moj_payload(payload, remaining)
        notes.append(
            f"MOJ {label} zip UpdateDate={payload.get('UpdateDate') or '?'} matched {len(matched)}"
        )
        for law in matched:
            loaded[law.pcode] = law
            remaining.discard(law.pcode)
    if remaining:
        logger.warning("MOJ missed %s; trying Hugging Face", sorted(remaining))
        hf_laws, hf_notes = load_hf_laws(set(remaining), raw_dir, refresh=refresh)
        notes.extend(hf_notes)
        for law in hf_laws:
            loaded[law.pcode] = law
            remaining.discard(law.pcode)
    ordered = [loaded[pcode] for pcode in pcodes if pcode in loaded]
    return ordered, sorted(remaining), notes


def ingest_raw_laws(
    session: Session,
    laws: list[RawLaw],
    catalog: Catalog,
    page_assets: dict[str, list[PageAsset]] | None = None,
    *,
    force: bool = False,
) -> IngestReport:
    report = IngestReport()
    pages = page_assets or {}
    changed = False
    for law in _parents_first(laws, catalog):
        cfg = catalog.by_pcode(law.pcode) or fallback_config(
            law.pcode, name=law.name, law_level=law.law_level
        )
        try:
            with session.begin_nested():
                status = _ingest_one(
                    session,
                    law,
                    cfg,
                    pages.get(law.pcode, []),
                    force=force,
                    warnings=report.warnings,
                )
        except Exception as exc:
            logger.exception("Failed to ingest %s", law.pcode)
            report.results.append(LawRunStatus(law.pcode, law.name, "error", str(exc)))
            continue
        report.results.append(status)
        if status.status in {"inserted", "forced"}:
            changed = True
    _link_parents(session, catalog)
    session.flush()
    if changed:
        rebuild_cross_references(session, catalog)
        session.flush()
    report.stats = collect_stats(session)
    return report


def rebuild_cross_references(session: Session, catalog: Catalog) -> None:
    session.execute(delete(CrossRef))
    laws = list(session.scalars(select(Law)))
    versions = list(session.scalars(select(LawVersion)))
    articles = list(session.scalars(select(Article).order_by(Article.version_id, Article.seq)))
    clauses = list(session.scalars(select(Clause).order_by(Clause.article_id, Clause.seq)))
    if not articles:
        return

    articles_by_version: dict[int, list[Article]] = defaultdict(list)
    for article in articles:
        articles_by_version[article.version_id].append(article)
    clauses_by_article: dict[int, list[Clause]] = defaultdict(list)
    for clause in clauses:
        clauses_by_article[article_id_of(clause)].append(clause)

    latest_id: dict[str, int] = {}
    latest_date: dict[str, tuple] = {}
    for version in versions:
        key = (version.modified_date, version.version_id)
        if version.pcode not in latest_date or key > latest_date[version.pcode]:
            latest_date[version.pcode] = key
            latest_id[version.pcode] = version.version_id

    latest_article_ids: dict[str, dict[tuple[int, int], int]] = defaultdict(dict)
    latest_clause_views: dict[int, list[ClauseView]] = {}
    for pcode, version_id in latest_id.items():
        for article in articles_by_version.get(version_id, []):
            latest_article_ids[pcode][(article.article_no_main, article.article_no_sub)] = (
                article.article_id
            )
            latest_clause_views[article.article_id] = [
                _view(clause) for clause in clauses_by_article.get(article.article_id, [])
            ]

    name_to_pcode = catalog.name_index(_name_extras(laws))
    known_names = sorted(name_to_pcode, key=len, reverse=True)
    law_by_pcode = {law.pcode: law for law in laws}

    for version in versions:
        law = law_by_pcode.get(version.pcode)
        if law is None:
            continue
        cfg = catalog.by_pcode(version.pcode) or fallback_config(
            version.pcode, name=law.name, law_level=law.law_level or ""
        )
        version_articles = articles_by_version.get(version.version_id, [])
        benfa_pcode, benfa_name = _benfa(cfg, version_articles, name_to_pcode, law_by_pcode)
        context = LawResolveContext(
            pcode=version.pcode,
            name=law.name,
            benfa_pcode=benfa_pcode,
            benfa_name=benfa_name,
            name_to_pcode=name_to_pcode,
            articles_by_pcode=latest_article_ids,
            clauses_by_article=latest_clause_views,
            ordered_keys=[
                (article.article_no_main, article.article_no_sub) for article in version_articles
            ],
            known_names=known_names,
        )
        for article in version_articles:
            views = [_view(clause) for clause in clauses_by_article.get(article.article_id, [])]
            hits = extract_refs_for_article(
                views,
                context,
                (article.article_no_main, article.article_no_sub),
            )
            for hit in hits:
                to_article_id = None
                to_clause_id = None
                if hit.target_pcode is not None and hit.target_main is not None:
                    to_article_id = latest_article_ids.get(hit.target_pcode, {}).get(
                        (hit.target_main, hit.target_sub or 0)
                    )
                if to_article_id is not None and hit.target_clause_path:
                    to_clause_id = _pick_clause(
                        latest_clause_views.get(to_article_id, []),
                        hit.target_clause_path,
                    )
                session.add(
                    CrossRef(
                        from_clause_id=hit.from_clause_id,
                        to_article_id=to_article_id,
                        to_clause_id=to_clause_id,
                        raw_span=hit.raw_span,
                        target_law_name=hit.target_law_name,
                        span_start=hit.span_start,
                        span_end=hit.span_end,
                    )
                )


def content_hash(law: RawLaw) -> str:
    payload = {
        "pcode": law.pcode,
        "name": law.name,
        "modified": law.modified_date_raw,
        "effective": law.effective_date_raw,
        "note": law.effective_note,
        "articles": [
            [article.article_type, article.article_no, article.content, article.heading_path]
            for article in law.articles
        ],
        "attachments": [
            [item.file_name, canonical_file_url(item.file_url)] for item in law.attachments
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ingest_one(
    session: Session,
    law: RawLaw,
    cfg: LawConfig,
    pages: list[PageAsset],
    *,
    force: bool,
    warnings: list[str],
) -> LawRunStatus:
    _upsert_law(session, law, cfg)
    session.flush()
    digest = content_hash(law)
    existing = session.scalar(
        select(LawVersion).where(
            LawVersion.pcode == law.pcode,
            LawVersion.modified_date == law.modified_date,
        )
    )
    if existing is not None and not force:
        if existing.content_hash != digest:
            message = (
                f"{law.pcode} content hash changed but LawModifiedDate "
                f"{law.modified_date.isoformat()} is unchanged; left the stored version in place. "
                "Pass --force to reparse it."
            )
            logger.warning(message)
            warnings.append(message)
            return LawRunStatus(law.pcode, law.name, "skipped", "content hash differs")
        return LawRunStatus(law.pcode, law.name, "skipped", "unchanged")
    status = "inserted"
    if existing is not None and force:
        session.delete(existing)
        session.flush()
        status = "forced"

    prepared, prepare_warnings = _prepare_articles(law)
    warnings.extend(prepare_warnings)
    version = LawVersion(
        pcode=law.pcode,
        modified_date=law.modified_date,
        effective_date=law.effective_date,
        effective_date_raw=law.effective_date_raw or None,
        effective_note=law.effective_note or None,
        histories=law.histories or None,
        foreword=law.foreword or None,
        content_hash=digest,
        source=law.source,
        source_update_date=law.source_update_date,
        snapshot_at=datetime.now(UTC),
    )
    session.add(version)
    session.flush()

    article_ids: dict[tuple[int, int], int] = {}
    for index, prepared_article in enumerate(prepared, start=1):
        row = Article(
            version_id=version.version_id,
            seq=index,
            article_no=prepared_article.article_no,
            article_no_main=prepared_article.main,
            article_no_sub=prepared_article.sub,
            heading_path=prepared_article.heading_path,
            full_text=prepared_article.full_text,
        )
        session.add(row)
        session.flush()
        article_ids[(prepared_article.main, prepared_article.sub)] = row.article_id
        for clause_index, clause in enumerate(prepared_article.clauses):
            session.add(
                Clause(
                    article_id=row.article_id,
                    seq=clause_index,
                    clause_path=clause.path,
                    is_proviso=clause.is_proviso,
                    text=clause.text,
                )
            )

    for asset in _merge_assets(law, pages):
        article_id = None
        if asset.article_main is not None:
            article_id = article_ids.get((asset.article_main, asset.article_sub or 0))
        caption_parts = [part for part in (asset.file_name, asset.warning) if part]
        session.add(
            FigureTable(
                version_id=version.version_id,
                article_id=article_id,
                media_type=media_type_of(asset.file_name, asset.source_url),
                source_url=asset.source_url,
                file_name=asset.file_name or None,
                storage_uri=None,
                caption_or_ocr=" | ".join(caption_parts) or None,
                source_kind=asset.source_kind,
            )
        )
    session.flush()
    logger.info(
        "%s %s %s (%s articles)",
        status,
        law.pcode,
        law.name,
        len(prepared),
    )
    return LawRunStatus(law.pcode, law.name, status, law.modified_date.isoformat())


def _prepare_articles(law: RawLaw) -> tuple[list[_PreparedArticle], list[str]]:
    stack: list[tuple[int, str]] = []
    prepared: list[_PreparedArticle] = []
    warnings: list[str] = []
    seen: set[tuple[int, int]] = set()
    for raw in law.articles:
        if raw.article_type == "C":
            update_heading_stack(stack, raw.content)
            continue
        if raw.article_type != "A":
            warnings.append(f"{law.pcode} skipped article type {raw.article_type!r}")
            continue
        key = label_to_key(raw.article_no)
        if key is None:
            warnings.append(f"{law.pcode} could not parse article number {raw.article_no!r}")
            continue
        if key in seen:
            warnings.append(f"{law.pcode} duplicate article {raw.article_no}; kept the first")
            continue
        seen.add(key)
        heading = raw.heading_path if raw.heading_path is not None else format_heading_path(stack)
        clauses = split_clauses(raw.content)
        if not clauses and raw.content.strip():
            clauses = [ParsedClause(path="1", text=raw.content.strip(), is_proviso=False)]
        prepared.append(
            _PreparedArticle(
                article_no=raw.article_no,
                main=key[0],
                sub=key[1],
                heading_path=heading,
                full_text=raw.content,
                clauses=clauses,
            )
        )
    return prepared, warnings


def _merge_assets(law: RawLaw, pages: list[PageAsset]) -> list[_MergedAsset]:
    merged: dict[str, _MergedAsset] = {}
    order: list[str] = []
    for item in law.attachments:
        url = canonical_file_url(item.file_url)
        key = file_id_of(url) or url
        main, sub = _numbers_in_filename(item.file_name)
        merged[key] = _MergedAsset(url, item.file_name, main, sub, None, "api")
        order.append(key)
    for page in pages:
        key = page.file_id or page.source_url
        if key in merged:
            current = merged[key]
            current.source_kind = "api+page"
            if page.warning:
                current.warning = page.warning
            if page.article_main is not None and current.article_main is None:
                current.article_main = page.article_main
                current.article_sub = page.article_sub or 0
            if page.file_name and current.file_name in {"", "完整條文"}:
                current.file_name = page.file_name
            continue
        merged[key] = _MergedAsset(
            page.source_url,
            page.file_name,
            page.article_main,
            page.article_sub,
            page.warning,
            "page",
        )
        order.append(key)
    return [merged[key] for key in order]


def _numbers_in_filename(name: str) -> tuple[int | None, int | None]:
    match = _ARTICLE_IN_NAME.search(name or "")
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2) or 0)


def _upsert_law(session: Session, law: RawLaw, cfg: LawConfig) -> Law:
    parent_pcode = cfg.parent_act_pcode
    if parent_pcode and session.get(Law, parent_pcode) is None:
        parent_pcode = None
    values = {
        "name": law.name,
        "doc_type": cfg.doc_type,
        "law_level": law.law_level or None,
        "category": law.category or None,
        "official_url": law.official_url,
        "parent_act_pcode": parent_pcode,
        "parent_act_name": cfg.parent_act_name,
    }
    row = session.get(Law, law.pcode)
    if row is None:
        row = Law(pcode=law.pcode, **values)
        session.add(row)
        return row
    for field_name, value in values.items():
        if getattr(row, field_name) != value:
            setattr(row, field_name, value)
    return row


def _link_parents(session: Session, catalog: Catalog) -> None:
    for cfg in catalog.laws:
        if not cfg.parent_act_pcode:
            continue
        row = session.get(Law, cfg.pcode)
        parent = session.get(Law, cfg.parent_act_pcode)
        if row is None or parent is None:
            continue
        if row.parent_act_pcode != parent.pcode:
            row.parent_act_pcode = parent.pcode
        if cfg.parent_act_name and row.parent_act_name != cfg.parent_act_name:
            row.parent_act_name = cfg.parent_act_name


def _parents_first(laws: list[RawLaw], catalog: Catalog) -> list[RawLaw]:
    by_pcode = {law.pcode: law for law in laws}
    seen: set[str] = set()
    ordered: list[RawLaw] = []

    def visit(pcode: str) -> None:
        if pcode in seen or pcode not in by_pcode:
            return
        seen.add(pcode)
        cfg = catalog.by_pcode(pcode)
        if cfg and cfg.parent_act_pcode:
            visit(cfg.parent_act_pcode)
        ordered.append(by_pcode[pcode])

    for law in laws:
        visit(law.pcode)
    return ordered


def _name_extras(laws: list[Law]) -> dict[str, str]:
    extra: dict[str, str] = {}
    for law in laws:
        extra[law.name] = law.pcode
        bare = _TRAILING_NOTE.sub("", law.name).strip()
        if bare:
            extra[bare] = law.pcode
    return extra


def _benfa(
    cfg: LawConfig,
    articles: list[Article],
    name_to_pcode: dict[str, str],
    law_by_pcode: dict[str, Law],
) -> tuple[str | None, str | None]:
    shorthand = find_shorthand("\n".join(article.full_text for article in articles))
    if "本法" in shorthand:
        name = shorthand["本法"]
        return name_to_pcode.get(name), name
    pcode = cfg.parent_act_pcode if cfg.parent_act_pcode in law_by_pcode else None
    name = cfg.parent_act_name
    if pcode is None and name:
        pcode = name_to_pcode.get(name)
    if pcode and not name:
        parent = law_by_pcode.get(pcode)
        name = parent.name if parent else None
    return pcode, name


def _view(clause: Clause) -> ClauseView:
    return ClauseView(
        clause_id=clause.clause_id,
        path=clause.clause_path,
        is_proviso=clause.is_proviso,
        text=clause.text,
    )


def _pick_clause(clauses: list[ClauseView], path: str) -> int | None:
    matches = [clause for clause in clauses if clause.path == path]
    plain = [clause for clause in matches if not clause.is_proviso]
    chosen = plain or matches
    return chosen[0].clause_id if chosen else None


def article_id_of(clause: Clause) -> int:
    return clause.article_id


def _write_report(path: Path, report: IngestReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "results": [
            {"pcode": item.pcode, "name": item.name, "status": item.status, "detail": item.detail}
            for item in report.results
        ],
        "stats": [row.as_dict() for row in report.stats],
        "missing_pcodes": report.missing_pcodes,
        "source_notes": report.source_notes,
        "warnings": report.warnings,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
