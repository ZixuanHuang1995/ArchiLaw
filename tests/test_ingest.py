"""Load a saved slice of the real MOJ payload. No network."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from twbc_rag.catalog import load_catalog
from twbc_rag.db.models import Article, Clause, CrossRef, Law, LawVersion
from twbc_rag.ingest.moj import parse_moj_payload
from twbc_rag.ingest.pipeline import ingest_raw_laws
from twbc_rag.parse.clauses import split_clauses

FIXTURE = Path(__file__).parent / "fixtures" / "moj_sample.json"


def _laws():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return parse_moj_payload(payload)


def test_ingest_matches_parser_and_is_idempotent(session: Session) -> None:
    laws = _laws()
    catalog = load_catalog()
    first = ingest_raw_laws(session, laws, catalog, page_assets={}, force=False)
    session.commit()

    assert {item.status for item in first.results} == {"inserted"}
    by_pcode = {row.pcode: row for row in first.stats}
    assert by_pcode["D0070114"].articles == 12
    assert by_pcode["D0070109"].articles == 6
    assert by_pcode["D0070114"].provisos >= 1
    assert by_pcode["D0070109"].provisos >= 1

    general = session.get(Law, "D0070114")
    assert general is not None
    assert general.parent_act_pcode == "D0070109"
    assert general.doc_type == "order"

    article_three = session.scalar(
        select(Article)
        .join(LawVersion)
        .where(LawVersion.pcode == "D0070109", Article.article_no == "第 3 條")
    )
    assert article_three is not None
    assert "總則" in article_three.heading_path
    stored = session.scalars(
        select(Clause).where(Clause.article_id == article_three.article_id).order_by(Clause.seq)
    ).all()
    assert [clause.clause_path for clause in stored] == ["1", "1/1", "1/2", "1/3", "2", "3"]

    for article in session.scalars(select(Article)).all():
        parsed = split_clauses(article.full_text)
        rows = session.scalars(
            select(Clause).where(Clause.article_id == article.article_id).order_by(Clause.seq)
        ).all()
        assert [(row.clause_path, row.is_proviso, row.text) for row in rows] == [
            (clause.path, clause.is_proviso, clause.text) for clause in parsed
        ]

    citation = session.scalar(select(CrossRef).where(CrossRef.raw_span.contains("第九十七條")))
    assert citation is not None and citation.to_article_id is not None
    target = session.get(Article, citation.to_article_id)
    assert target is not None
    assert (target.article_no_main, target.article_no_sub) == (97, 0)

    proviso = session.scalar(
        select(Clause)
        .join(Article)
        .join(LawVersion)
        .where(
            LawVersion.pcode == "D0070109",
            Article.article_no == "第 9 條",
            Clause.is_proviso.is_(True),
        )
    )
    assert proviso is not None
    assert proviso.text.startswith("但")

    snapshots = {row.pcode: row.snapshot_at for row in session.scalars(select(LawVersion)).all()}
    second = ingest_raw_laws(session, laws, catalog, page_assets={}, force=False)
    session.commit()
    assert {item.status for item in second.results} == {"skipped"}
    assert session.scalar(select(func.count()).select_from(LawVersion)) == 2
    again = {row.pcode: row.snapshot_at for row in session.scalars(select(LawVersion)).all()}
    assert again == snapshots

    mutated = deepcopy(laws)
    for law in mutated:
        if law.pcode == "D0070114":
            law.modified_date = date(2026, 1, 1)
            law.modified_date_raw = "20260101"
    third = ingest_raw_laws(session, mutated, catalog, page_assets={}, force=False)
    session.commit()
    assert any(item.pcode == "D0070114" and item.status == "inserted" for item in third.results)
    versions = session.scalar(
        select(func.count()).select_from(LawVersion).where(LawVersion.pcode == "D0070114")
    )
    assert versions == 2
    # The older version is still there; the latest text is the one just inserted.
    latest = session.scalar(
        select(LawVersion)
        .where(LawVersion.pcode == "D0070114")
        .order_by(LawVersion.modified_date.desc())
    )
    assert latest is not None
    assert latest.modified_date == date(2026, 1, 1)
