"""JSONL snapshot of the latest version of each law, for later chunking."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from twbc_rag.db.models import Article, Clause, Law, LawVersion


def export_jsonl(session: Session, dest: Path) -> tuple[Path, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    articles_path = dest / "articles.jsonl"
    clauses_path = dest / "clauses.jsonl"
    latest = _latest_versions(session)
    with (
        articles_path.open("w", encoding="utf-8") as article_file,
        clauses_path.open("w", encoding="utf-8") as clause_file,
    ):
        for version, law in latest:
            articles = session.scalars(
                select(Article)
                .where(Article.version_id == version.version_id)
                .order_by(Article.seq)
            ).all()
            for article in articles:
                article_file.write(
                    json.dumps(
                        {
                            "pcode": law.pcode,
                            "law_name": law.name,
                            "doc_type": law.doc_type,
                            "official_url": law.official_url,
                            "version_id": version.version_id,
                            "modified_date": version.modified_date.isoformat(),
                            "effective_date": (
                                version.effective_date.isoformat()
                                if version.effective_date
                                else None
                            ),
                            "effective_note": version.effective_note or "",
                            "article_id": article.article_id,
                            "seq": article.seq,
                            "article_no": article.article_no,
                            "article_no_main": article.article_no_main,
                            "article_no_sub": article.article_no_sub,
                            "heading_path": article.heading_path,
                            "full_text": article.full_text,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                clauses = session.scalars(
                    select(Clause)
                    .where(Clause.article_id == article.article_id)
                    .order_by(Clause.seq)
                ).all()
                for clause in clauses:
                    clause_file.write(
                        json.dumps(
                            {
                                "clause_id": clause.clause_id,
                                "article_id": article.article_id,
                                "pcode": law.pcode,
                                "law_name": law.name,
                                "article_no": article.article_no,
                                "heading_path": article.heading_path,
                                "seq": clause.seq,
                                "clause_path": clause.clause_path,
                                "is_proviso": clause.is_proviso,
                                "text": clause.text,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
    return articles_path, clauses_path


def _latest_versions(session: Session) -> list[tuple[LawVersion, Law]]:
    versions = session.scalars(select(LawVersion)).all()
    laws = {law.pcode: law for law in session.scalars(select(Law)).all()}
    chosen: dict[str, LawVersion] = {}
    for version in versions:
        current = chosen.get(version.pcode)
        if current is None or (version.modified_date, version.version_id) > (
            current.modified_date,
            current.version_id,
        ):
            chosen[version.pcode] = version
    return [(version, laws[version.pcode]) for version in chosen.values() if version.pcode in laws]
