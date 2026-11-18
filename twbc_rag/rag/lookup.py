"""Structured article-number lookup.

Queries like 「設計施工編第110-1條」 or 「第一百十條之一」 resolve to chunk ids
before BM25 or dense search. A bare 「第1條」 is ignored when every law has
one, so the channel does not drown the ranking.
"""

from __future__ import annotations

import re

from twbc_rag.parse.crossref import parse_article_number
from twbc_rag.rag.chunks import Chunk

_DIGIT = r"[0-9０-９]+"
_CN = r"[一二三四五六七八九十百千萬○〇零◯兩两]+"
_NUM = rf"(?:{_DIGIT}\s*[-－]\s*{_DIGIT}|{_DIGIT}|{_CN})"
_SIMPLE = rf"(?:{_DIGIT}|{_CN})"
_ARTICLE = re.compile(rf"第\s*(?P<num>{_NUM})\s*條(?:\s*之\s*(?P<suf>{_SIMPLE}))?")


def lookup_chunk_ids(query: str, chunks: list[Chunk]) -> list[str]:
    numbers = _article_numbers(query)
    if not numbers:
        return []
    pcode = _law_pcode(query, chunks)
    found: list[str] = []
    for main, sub in numbers:
        matches = [
            chunk
            for chunk in chunks
            if chunk.article_no_main == main
            and chunk.article_no_sub == sub
            and (pcode is None or chunk.pcode == pcode)
        ]
        if pcode is None and sub == 0 and main < 100 and len(matches) != 1:
            continue
        if pcode is None and len(matches) > 4:
            continue
        for chunk in matches:
            if chunk.chunk_id not in found:
                found.append(chunk.chunk_id)
    return found


def _article_numbers(query: str) -> list[tuple[int, int]]:
    numbers: list[tuple[int, int]] = []
    for match in _ARTICLE.finditer(query or ""):
        parsed = parse_article_number(match.group("num"), match.group("suf"))
        if parsed and parsed not in numbers:
            numbers.append(parsed)
    return numbers


def _law_pcode(query: str, chunks: list[Chunk]) -> str | None:
    aliases: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        for alias in chunk.aliases:
            key = (alias, chunk.pcode)
            if alias and key not in seen:
                seen.add(key)
                aliases.append(key)
    aliases.sort(key=lambda item: len(item[0]), reverse=True)
    for alias, pcode in aliases:
        if alias in query:
            return pcode
    return None
