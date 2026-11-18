"""Hugging Face fallback: lianghsun/tw-law.

The dataset is a weekly mirror of the same MOJ API, flattened to one JSONL
row per article. It lags the official zip (the card we checked was still on
the 2026-04-24 UpdateDate while MOJ's zip was 2026-11-06) and it drops the
chapter-only rows, but each article already carries ``heading_path``.
Pcodes are recovered from ``url``.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from twbc_rag.ingest.dates import parse_moj_date
from twbc_rag.ingest.http_client import download_cached
from twbc_rag.ingest.moj import pcode_from_url
from twbc_rag.ingest.types import RawArticle, RawAttachment, RawLaw, SourceError

logger = logging.getLogger(__name__)

HF_LAW_URL = (
    "https://huggingface.co/datasets/lianghsun/tw-law/resolve/main/ch_law_articles/train.jsonl"
)
HF_ORDER_URL = (
    "https://huggingface.co/datasets/lianghsun/tw-law/resolve/main/ch_order_articles/train.jsonl"
)


def load_hf_laws(
    pcodes: set[str],
    raw_dir: Path,
    *,
    refresh: bool,
) -> tuple[list[RawLaw], list[str]]:
    notes: list[str] = []
    remaining = set(pcodes)
    found: dict[str, RawLaw] = {}
    for label, url, filename in (
        ("ch_law_articles", HF_LAW_URL, "hf_ch_law_articles.jsonl"),
        ("ch_order_articles", HF_ORDER_URL, "hf_ch_order_articles.jsonl"),
    ):
        if not remaining:
            break
        dest = raw_dir / filename
        try:
            download_cached(url, dest, refresh=refresh)
        except SourceError as exc:
            notes.append(f"Hugging Face {label} failed: {exc}")
            continue
        parsed = parse_hf_jsonl(dest, remaining)
        notes.append(f"Hugging Face {label}: matched {len(parsed)} pcode(s)")
        for law in parsed:
            found[law.pcode] = law
            remaining.discard(law.pcode)
    ordered = [found[pcode] for pcode in pcodes if pcode in found]
    return ordered, notes


def parse_hf_jsonl(path: Path, pcodes: set[str]) -> list[RawLaw]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSONL at %s:%s", path, line_no)
                continue
            pcode = pcode_from_url(str(record.get("url") or ""))
            if pcode and pcode in pcodes:
                grouped[pcode].append(record)
    return [assemble_hf_law(pcode, rows) for pcode, rows in grouped.items()]


def assemble_hf_law(pcode: str, rows: list[dict]) -> RawLaw:
    first = rows[0]
    modified, modified_raw = parse_moj_date(str(first.get("modified_date") or ""))
    if modified is None:
        raise SourceError(f"HF record {pcode} has no modified_date")
    effective, effective_raw = parse_moj_date(str(first.get("effective_date") or ""))
    attachments: list[RawAttachment] = []
    seen_urls: set[str] = set()
    for row in rows:
        for item in row.get("attachments") or []:
            if not isinstance(item, dict):
                continue
            file_url = str(item.get("FileURL") or item.get("file_url") or "").strip()
            if not file_url or file_url in seen_urls:
                continue
            seen_urls.add(file_url)
            attachments.append(
                RawAttachment(
                    file_name=str(item.get("FileName") or item.get("file_name") or "").strip(),
                    file_url=file_url,
                )
            )
    articles: list[RawArticle] = []
    seen_numbers: set[str] = set()
    for row in rows:
        article_no = str(row.get("article_no") or "").strip()
        if not article_no or article_no in seen_numbers:
            continue
        seen_numbers.add(article_no)
        heading = row.get("heading_path") or []
        if isinstance(heading, list):
            heading_path = " / ".join(str(part).strip() for part in heading if str(part).strip())
        else:
            heading_path = str(heading or "")
        articles.append(
            RawArticle(
                article_type=str(row.get("article_type") or "A"),
                article_no=article_no,
                content=_article_body(str(row.get("text") or "")),
                heading_path=heading_path,
            )
        )
    return RawLaw(
        pcode=pcode,
        name=str(first.get("name") or pcode).strip(),
        law_level=str(first.get("level") or "").strip(),
        official_url=str(
            first.get("url") or f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}"
        ),
        category=str(first.get("category") or "").strip(),
        modified_date=modified,
        modified_date_raw=modified_raw or str(first.get("modified_date") or ""),
        effective_date=effective,
        effective_date_raw=effective_raw,
        effective_note=str(first.get("effective_note") or "").strip(),
        histories=str(first.get("histories") or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip(),
        foreword="",
        attachments=attachments,
        articles=articles,
        source="huggingface",
        source_update_date=str(first.get("update_date") or "") or None,
    )


def _article_body(text: str) -> str:
    marker = "【條號】"
    index = text.find(marker)
    if index == -1:
        return text
    rest = text[index:]
    newline = rest.find("\n")
    if newline == -1:
        return ""
    return rest[newline + 1 :].replace("\r\n", "\n").replace("\r", "\n")
