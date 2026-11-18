"""編 / 章 / 節 stack.

MOJ stores headings as ArticleType "C" rows interleaved with articles.
Indentation is unreliable (some 編 rows are not indented), so the level
character decides the rank. 「第 六 節之一」 is still a 節.
"""

from __future__ import annotations

import re

_LEVEL = {"編": 1, "章": 2, "節": 3, "款": 4, "目": 5}
_HEADING = re.compile(
    r"第\s*([0-9０-９一二三四五六七八九十百千萬○〇零◯兩两]+)\s*"
    r"(編|章|節|款|目)"
    r"(?:之\s*([0-9０-９一二三四五六七八九十]+))?"
)


def normalize_heading(text: str) -> str:
    return " ".join(text.split())


def update_heading_stack(stack: list[tuple[int, str]], text: str) -> None:
    label = normalize_heading(text)
    if not label:
        return
    match = _HEADING.search(label)
    rank = _LEVEL[match.group(2)] if match else 99
    while stack and stack[-1][0] >= rank:
        stack.pop()
    stack.append((rank, label))


def format_heading_path(stack: list[tuple[int, str]]) -> str:
    return " / ".join(label for _, label in stack)
