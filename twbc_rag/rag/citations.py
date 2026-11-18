"""Check 【法規 第N條第M項】 citations against retrieved evidence."""

from __future__ import annotations

import re

from twbc_rag.parse.crossref import parse_article_number
from twbc_rag.parse.numerals import parse_chinese_numeral
from twbc_rag.rag.chunks import Chunk

_CITATION = re.compile(r"【([^】]+)】")
_DIGIT = r"[0-9０-９]+"
_CN = r"[一二三四五六七八九十百千萬○〇零◯兩两]+"
_NUM = rf"(?:{_DIGIT}\s*[-－]\s*{_DIGIT}|{_DIGIT}|{_CN})"
_SIMPLE = rf"(?:{_DIGIT}|{_CN})"
_ARTICLE = re.compile(rf"第\s*(?P<num>{_NUM})\s*條(?:\s*之\s*(?P<suf>{_SIMPLE}))?")
_ITEM = re.compile(rf"第\s*(?P<num>{_SIMPLE})\s*項")

REFUSAL = "無法依目前檢索到的條文回答這個問題。"


def check_citations(answer: str, evidence: list[Chunk]) -> tuple[str, list[str], list[str]]:
    """Return cleaned answer, supported citation labels, and dropped spans."""
    supported: list[str] = []
    dropped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        span = match.group(1).strip()
        label = _supported_label(span, evidence)
        if label is None:
            dropped.append(span)
            return ""
        if label not in supported:
            supported.append(label)
        return f"【{label}】"

    cleaned = _CITATION.sub(replace, answer or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, supported, dropped


def _supported_label(span: str, evidence: list[Chunk]) -> str | None:
    match = _ARTICLE.search(span)
    if not match:
        return None
    parsed = parse_article_number(match.group("num"), match.group("suf"))
    if parsed is None:
        return None
    main, sub = parsed
    item = _item_number(span)
    candidates = [
        chunk
        for chunk in evidence
        if chunk.article_no_main == main
        and chunk.article_no_sub == sub
        and _names_match(span, chunk)
    ]
    if not candidates:
        same_number = [
            chunk
            for chunk in evidence
            if chunk.article_no_main == main and chunk.article_no_sub == sub
        ]
        if len(same_number) == 1 and not _looks_like_named_law(span):
            candidates = same_number
    for chunk in candidates:
        if item is None or _has_item(chunk, item):
            path = str(item) if item is not None else None
            return chunk.citation_label(path)
    return None


def _names_match(span: str, chunk: Chunk) -> bool:
    return any(alias and alias in span for alias in chunk.aliases)


def _looks_like_named_law(span: str) -> bool:
    return any(span.endswith(suffix) or suffix in span for suffix in ("法", "編", "標準", "規則"))


def _item_number(span: str) -> int | None:
    match = _ITEM.search(span)
    if not match:
        return None
    return parse_chinese_numeral(match.group("num"))


def _has_item(chunk: Chunk, item: int) -> bool:
    for child in chunk.clauses:
        head = child.clause_path.split("/", 1)[0]
        if head.isdigit() and int(head) == item:
            return True
    return False
