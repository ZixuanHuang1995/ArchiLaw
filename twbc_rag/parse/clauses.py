"""Split an article into 項 / 款 / 目 and pull 但書 out as their own clauses.

MOJ separates both 項 and 款 with newlines, so a blank-line split is not enough.
A line is a 款 when it starts with 「一、」「十一、」…, a 目 when it starts with
「（一）」, and anything else starts a new 項. Box-drawing lines are tables;
they stay glued to the clause they follow. Cells often contain 「（一）」 and
must not become 目.

clause_path is slash-separated integers: 項, then 款, then 目.
A proviso keeps that path and sets is_proviso. The article's full_text is
stored unchanged; only the clause rows are split.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from twbc_rag.parse.numerals import parse_chinese_numeral

_BOX = set("┌┐└┘├┤┬┴┼─│━┃┏┓┗┛┣┫┳┻╋")
_KUAN = re.compile(r"^([一二三四五六七八九十百千萬○〇零◯兩两]+|\d+)[、，,]")
_MU = re.compile(r"^[（(]([一二三四五六七八九十百千萬○〇零◯兩两]+|\d+)[）)]")
_PROVISO = re.compile(r"(?<=[。；])\s*(?=但)")


@dataclass
class ParsedClause:
    path: str
    text: str
    is_proviso: bool


@dataclass
class _Node:
    kind: str
    number: int
    lines: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)


def split_clauses(article_text: str) -> list[ParsedClause]:
    lines = _logical_lines(article_text)
    if not lines:
        return []
    roots = _build_tree(lines)
    clauses: list[ParsedClause] = []
    for root in roots:
        _emit(root, str(root.number), clauses)
    return clauses


def _logical_lines(article_text: str) -> list[str]:
    normalized = article_text.replace("\r\n", "\n").replace("\r", "\n")
    return [line for line in normalized.split("\n") if line.strip()]


def _is_table_line(line: str) -> bool:
    return any(char in _BOX for char in line)


def _build_tree(lines: list[str]) -> list[_Node]:
    roots: list[_Node] = []
    current_item: _Node | None = None
    current_kuan: _Node | None = None

    for line in lines:
        if _is_table_line(line):
            target = current_kuan or current_item
            if target is None:
                current_item = _Node(kind="item", number=1)
                roots.append(current_item)
                target = current_item
            target.lines.append(line)
            continue

        stripped = line.strip()
        mu_match = _MU.match(stripped)
        kuan_match = None if mu_match else _KUAN.match(stripped)
        if mu_match:
            number = parse_chinese_numeral(mu_match.group(1))
            if number is None:
                _start_item(roots, stripped)
                current_item = roots[-1]
                current_kuan = None
                continue
            parent = current_kuan or current_item
            if parent is None:
                current_item = _Node(kind="item", number=1)
                roots.append(current_item)
                parent = current_item
            parent.children.append(_Node(kind="mu", number=number, lines=[stripped]))
            continue
        if kuan_match:
            number = parse_chinese_numeral(kuan_match.group(1))
            if number is None:
                current_item = _start_item(roots, stripped)
                current_kuan = None
                continue
            if current_item is None:
                current_item = _Node(kind="item", number=1)
                roots.append(current_item)
            node = _Node(kind="kuan", number=number, lines=[stripped])
            current_item.children.append(node)
            current_kuan = node
            continue

        current_item = _start_item(roots, stripped)
        current_kuan = None
    return roots


def _start_item(roots: list[_Node], text: str) -> _Node:
    node = _Node(kind="item", number=len(roots) + 1, lines=[text])
    roots.append(node)
    return node


def _emit(node: _Node, path: str, out: list[ParsedClause]) -> None:
    text = "\n".join(node.lines).strip()
    if text:
        main, provisos = split_proviso(text)
        if main:
            out.append(ParsedClause(path=path, text=main, is_proviso=False))
        for proviso in provisos:
            out.append(ParsedClause(path=path, text=proviso, is_proviso=True))
        if not main and not provisos:
            out.append(ParsedClause(path=path, text=text, is_proviso=False))
    for child in node.children:
        _emit(child, f"{path}/{child.number}", out)


def split_proviso(text: str) -> tuple[str, list[str]]:
    """Cut sentence-initial 但 off the clause. Tables stay with the main text."""
    table_at = next((index for index, char in enumerate(text) if char in _BOX), None)
    head, tail = (text, "") if table_at is None else (text[:table_at], text[table_at:])
    parts = _PROVISO.split(head)
    mains: list[str] = []
    provisos: list[str] = []
    for part in parts:
        if part.startswith("但"):
            stripped = part.strip()
            if stripped:
                provisos.append(stripped)
        else:
            mains.append(part)
    main = "".join(mains).strip()
    if tail:
        main = f"{main}\n{tail}".strip() if main else tail.strip()
    return main, provisos
