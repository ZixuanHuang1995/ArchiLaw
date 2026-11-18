"""Citation extraction and resolution.

Resolved means we can point at an article row (to_article_id). 前項 / 前款
also fill to_clause_id when the paragraph exists. A named law that is not in
the corpus (都市計畫法, 消防法, …) is stored with target_law_name and a null
article id.

「本法」 is not the current instrument when the current instrument is an
order. 建築技術規則 says 建築法（以下簡稱本法）; the design book then cites
本法第十一條. The in-text definition wins, otherwise the catalog's
parent_act_* is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from twbc_rag.parse.numerals import parse_chinese_numeral

_DIGIT = r"[0-9０-９]+"
_CN = r"[一二三四五六七八九十百千萬○〇零◯兩两]+"
_NUM = rf"(?:{_DIGIT}\s*[-－]\s*{_DIGIT}|{_DIGIT}|{_CN})"
_SIMPLE = rf"(?:{_DIGIT}|{_CN})"

_ARTICLE = re.compile(
    rf"第\s*(?P<num>{_NUM})\s*條"
    rf"(?:\s*之\s*(?P<suf>{_SIMPLE}))?"
    rf"(?:\s*第\s*(?P<item>{_SIMPLE})\s*項)?"
    rf"(?:\s*第\s*(?P<kuan>{_SIMPLE})\s*款"
    rf"(?:\s*至\s*第\s*(?P<kuan_to>{_SIMPLE})\s*款)?)?"
    rf"(?:\s*第\s*(?P<mu>{_SIMPLE})\s*目)?"
)
_INTRA = re.compile(
    rf"第\s*(?P<item>{_SIMPLE})\s*項第\s*(?P<kuan>{_SIMPLE})\s*款"
    rf"|第\s*(?P<kuan2>{_SIMPLE})\s*款第\s*(?P<mu>{_SIMPLE})\s*目"
    rf"|第\s*(?P<item_only>{_SIMPLE})\s*項"
    rf"|第\s*(?P<kuan_only>{_SIMPLE})\s*款"
    rf"|第\s*(?P<mu_only>{_SIMPLE})\s*目"
)
_RELATIVE = re.compile(r"(前條|後條|次條|本條|同條|前項|次項|前款|前目)(?!文)")
_SHORTHAND = re.compile(r"([一-龥]{2,40})[（(]以下簡稱(本法|本規則|本標準|本編)[）)]")
_LAW_SUFFIXES = (
    "條例",
    "通則",
    "細則",
    "規則",
    "辦法",
    "綱要",
    "標準",
    "準則",
    "規程",
    "法律",
    "法",
    "律",
    "編",
)
# Single-character cuts are limited to prefixes that are not common inside titles.
# 「有」 would slice 國有財產法 down to 財產法.
_PARTICLES = (
    "有關",
    "關於",
    "依照",
    "依據",
    "按照",
    "根據",
    "準用",
    "適用",
    "比照",
    "違反",
    "依",
    "按",
    "照",
    "及",
    "與",
    "或",
    "於",
)
_CURRENT_TOKENS = {"本編", "同編", "本章", "本節", "本辦法", "本規則", "本標準"}
_LABEL = re.compile(r"^第\s*(\d+)\s*(?:-\s*(\d+))?\s*條$")


@dataclass(frozen=True)
class ClauseView:
    clause_id: int
    path: str
    is_proviso: bool
    text: str


@dataclass(frozen=True)
class LawResolveContext:
    pcode: str
    name: str
    benfa_pcode: str | None
    benfa_name: str | None
    name_to_pcode: dict[str, str]
    # Latest version only: (main, sub) -> article_id, and that article's clauses.
    articles_by_pcode: dict[str, dict[tuple[int, int], int]]
    clauses_by_article: dict[int, list[ClauseView]]
    ordered_keys: list[tuple[int, int]]
    known_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RefHit:
    from_clause_id: int
    raw_span: str
    span_start: int
    span_end: int
    target_pcode: str | None
    target_main: int | None
    target_sub: int | None
    target_clause_path: str | None
    target_law_name: str | None


def parse_article_label(label: str) -> tuple[int, int] | None:
    """Official MOJ labels look like ``第 110-1 條`` or ``第 3 條``."""
    match = _LABEL.match(label.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


def parse_article_number(num: str, suffix: str | None) -> tuple[int, int] | None:
    num = num.strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    hyphen = re.fullmatch(r"(\d+)\s*[-－]\s*(\d+)", num)
    if hyphen:
        main, sub = int(hyphen.group(1)), int(hyphen.group(2))
    elif re.fullmatch(r"\d+", num):
        main, sub = int(num), 0
    else:
        parsed = parse_chinese_numeral(num)
        if parsed is None:
            return None
        main, sub = parsed, 0
    if suffix:
        suffix_value = parse_chinese_numeral(suffix)
        if suffix_value is None:
            return None
        sub = suffix_value
    return main, sub


def find_shorthand(text: str) -> dict[str, str]:
    """Map 本法/本規則/… to the name introduced by 「（以下簡稱…）」."""
    found: dict[str, str] = {}
    for match in _SHORTHAND.finditer(text):
        found[match.group(2)] = clean_law_name(match.group(1))
    return found


def clean_law_name(chunk: str) -> str:
    """Drop a leading verb so 「本規則依建築法」 becomes 「建築法」."""
    chunk = chunk.strip()
    best_at = -1
    best = chunk
    for particle in _PARTICLES:
        start = 0
        while True:
            index = chunk.find(particle, start)
            if index == -1:
                break
            rest = chunk[index + len(particle) :]
            cut_at = index + len(particle)
            if (
                rest
                and _looks_like_law(rest)
                and (cut_at > best_at or (cut_at == best_at and len(rest) < len(best)))
            ):
                best_at = cut_at
                best = rest
            start = index + len(particle)
    return best


def looks_like_law_name(name: str) -> bool:
    return _looks_like_law(name)


def extract_refs_for_article(
    clauses: list[ClauseView],
    ctx: LawResolveContext,
    article_key: tuple[int, int],
) -> list[RefHit]:
    """Resolve cites inside one article. ``article_key`` is that article's (main, sub)."""
    bound = _BoundContext(ctx, article_key)
    hits: list[RefHit] = []
    current_index = {key: index for index, key in enumerate(ctx.ordered_keys)}
    for clause in clauses:
        if not clause.text:
            continue
        hits.extend(_refs_in_clause(clause, bound, current_index))
    return hits


def _refs_in_clause(
    clause: ClauseView,
    ctx: LawResolveContext,
    current_index: dict[tuple[int, int], int],
) -> list[RefHit]:
    text = clause.text
    hits: list[RefHit] = []
    masks: list[tuple[int, int]] = []
    for match in _ARTICLE.finditer(text):
        parsed = parse_article_number(match.group("num"), match.group("suf"))
        if parsed is None:
            continue
        main, sub = parsed
        prefix = text[max(0, match.start() - 40) : match.start()]
        prefix_hit = _match_prefix(prefix, ctx.known_names)
        span_start = match.start()
        if prefix_hit and prefix.rstrip().endswith(prefix_hit[0]):
            span_start = match.start() - len(prefix_hit[0])
        token = prefix_hit[1] if prefix_hit else None
        target_pcode, target_name = _resolve_token(token, ctx)
        article_id = None
        if target_pcode is not None:
            article_id = ctx.articles_by_pcode.get(target_pcode, {}).get((main, sub))
        if article_id is None:
            target_pcode_out = None
        else:
            target_pcode_out = target_pcode
        kuan = _simple_int(match.group("kuan"))
        kuan_to = _simple_int(match.group("kuan_to"))
        item = _simple_int(match.group("item"))
        mu = _simple_int(match.group("mu"))
        span_end = match.end()
        raw = text[span_start:span_end]
        hits.append(
            _hit(
                clause,
                raw,
                span_start,
                span_end,
                target_pcode_out,
                main if target_pcode_out else None,
                sub if target_pcode_out else None,
                _clause_path_for(ctx, article_id, item=item, kuan=kuan, mu=mu),
                target_name,
            )
        )
        if kuan_to is not None and kuan_to != kuan:
            hits.append(
                _hit(
                    clause,
                    raw,
                    span_start,
                    span_end,
                    target_pcode_out,
                    main if target_pcode_out else None,
                    sub if target_pcode_out else None,
                    _clause_path_for(ctx, article_id, item=item, kuan=kuan_to, mu=None),
                    target_name,
                )
            )
        masks.append((span_start, span_end))

    for match in _RELATIVE.finditer(text):
        if _overlaps(masks, match.start(), match.end()):
            continue
        token = match.group(1)
        target = _resolve_relative(token, clause, ctx, current_index)
        hits.append(
            _hit(
                clause,
                token,
                match.start(),
                match.end(),
                target[0],
                target[1],
                target[2],
                target[3],
                target[4],
            )
        )
        masks.append((match.start(), match.end()))

    for match in _INTRA.finditer(text):
        if _overlaps(masks, match.start(), match.end()):
            continue
        article_id = _current_article_id(ctx)
        path = _intra_path(match, ctx, article_id)
        pcode = ctx.pcode if article_id is not None else None
        key = _current_key(ctx)
        hits.append(
            _hit(
                clause,
                match.group(0),
                match.start(),
                match.end(),
                pcode,
                key[0] if key and pcode else None,
                key[1] if key and pcode else None,
                path,
                ctx.name,
            )
        )
        masks.append((match.start(), match.end()))
    return hits


def _hit(
    clause: ClauseView,
    raw: str,
    start: int,
    end: int,
    pcode: str | None,
    main: int | None,
    sub: int | None,
    path: str | None,
    name: str | None,
) -> RefHit:
    return RefHit(
        from_clause_id=clause.clause_id,
        raw_span=raw,
        span_start=start,
        span_end=end,
        target_pcode=pcode,
        target_main=main,
        target_sub=sub,
        target_clause_path=path,
        target_law_name=name,
    )


def _match_prefix(prefix: str, known_names: list[str]) -> tuple[str, str] | None:
    """Return (exact suffix to include in the span, law token)."""
    text = prefix.rstrip()
    shorthand = re.search(
        r"([一-龥]{2,40}[（(]以下簡稱(?:本法|本規則|本標準|本編)[）)])$",
        text,
    )
    if shorthand:
        suffix = shorthand.group(1)
        inner = re.match(r"([一-龥]{2,40})", suffix)
        token = clean_law_name(inner.group(1) if inner else suffix)
        return suffix, token
    for name in known_names:
        if text.endswith(name):
            return name, name
    for token in ("同編", "本編", "本法", "本規則", "本標準", "本辦法", "本章", "本節"):
        if text.endswith(token):
            return token, token
    tail = re.search(r"([一-龥]{2,30})$", text)
    if tail:
        cleaned = clean_law_name(tail.group(1))
        if (
            _looks_like_law(cleaned)
            and cleaned not in _CURRENT_TOKENS | {"本法"}
            and text.endswith(cleaned)
        ):
            return cleaned, cleaned
    return None


def _resolve_token(token: str | None, ctx: LawResolveContext) -> tuple[str | None, str | None]:
    if token is None:
        return ctx.pcode, ctx.name
    if token in _CURRENT_TOKENS:
        return ctx.pcode, ctx.name
    if token == "本法":
        if ctx.benfa_pcode:
            return ctx.benfa_pcode, ctx.benfa_name or "本法"
        if ctx.benfa_name and ctx.benfa_name in ctx.name_to_pcode:
            return ctx.name_to_pcode[ctx.benfa_name], ctx.benfa_name
        return None, ctx.benfa_name or "本法"
    if token in ctx.name_to_pcode:
        return ctx.name_to_pcode[token], token
    return None, token


def _resolve_relative(
    token: str,
    clause: ClauseView,
    ctx: LawResolveContext,
    current_index: dict[tuple[int, int], int],
) -> tuple[str | None, int | None, int | None, str | None, str | None]:
    key = _current_key(ctx)
    if key is None:
        return None, None, None, None, ctx.name
    main, sub = key
    if token in {"本條", "同條"}:
        return ctx.pcode, main, sub, None, ctx.name
    if token in {"前條", "後條", "次條"}:
        index = current_index.get(key)
        if index is None:
            return None, None, None, None, ctx.name
        step = -1 if token == "前條" else 1
        neighbor = index + step
        if neighbor < 0 or neighbor >= len(ctx.ordered_keys):
            return None, None, None, None, ctx.name
        other = ctx.ordered_keys[neighbor]
        return ctx.pcode, other[0], other[1], None, ctx.name
    if token == "前項":
        item = _path_item(clause.path)
        if item is None or item <= 1:
            return ctx.pcode, main, sub, None, ctx.name
        return ctx.pcode, main, sub, str(item - 1), ctx.name
    if token == "次項":
        item = _path_item(clause.path)
        if item is None:
            return ctx.pcode, main, sub, None, ctx.name
        return ctx.pcode, main, sub, str(item + 1), ctx.name
    if token == "前款":
        path = _previous_sibling(clause.path)
        return ctx.pcode, main, sub, path, ctx.name
    if token == "前目":
        path = _previous_sibling(clause.path)
        return ctx.pcode, main, sub, path, ctx.name
    return None, None, None, None, ctx.name


def _current_key(ctx: object) -> tuple[int, int] | None:
    return getattr(ctx, "_current_key", None)


def _current_article_id(ctx: object) -> int | None:
    key = _current_key(ctx)
    pcode = getattr(ctx, "pcode", None)
    articles = getattr(ctx, "articles_by_pcode", {})
    if key is None or pcode is None:
        return None
    return articles.get(pcode, {}).get(key)


class _BoundContext:
    """LawResolveContext plus the article currently being scanned."""

    def __init__(self, ctx: LawResolveContext, article_key: tuple[int, int]) -> None:
        self.pcode = ctx.pcode
        self.name = ctx.name
        self.benfa_pcode = ctx.benfa_pcode
        self.benfa_name = ctx.benfa_name
        self.name_to_pcode = ctx.name_to_pcode
        self.articles_by_pcode = ctx.articles_by_pcode
        self.clauses_by_article = ctx.clauses_by_article
        self.ordered_keys = ctx.ordered_keys
        self.known_names = ctx.known_names
        self._current_key = article_key


def _simple_int(token: str | None) -> int | None:
    if not token:
        return None
    return parse_chinese_numeral(token)


def _clause_path_for(
    ctx: LawResolveContext,
    article_id: int | None,
    *,
    item: int | None,
    kuan: int | None,
    mu: int | None,
) -> str | None:
    if article_id is None:
        return None
    clauses = [
        clause for clause in ctx.clauses_by_article.get(article_id, []) if not clause.is_proviso
    ]
    if item is not None and kuan is None and mu is None:
        path = str(item)
        return path if any(clause.path == path for clause in clauses) else None
    if kuan is None and mu is None:
        return None
    matches: list[str] = []
    for clause in clauses:
        parts = clause.path.split("/")
        if mu is not None and kuan is not None:
            if len(parts) == 3 and _int_eq(parts[1], kuan) and _int_eq(parts[2], mu):
                if item is None or _int_eq(parts[0], item):
                    matches.append(clause.path)
        elif kuan is not None and mu is None:
            if (
                len(parts) >= 2
                and _int_eq(parts[1], kuan)
                and (item is None or _int_eq(parts[0], item))
            ):
                # Prefer the 款 node itself over a 目 that shares the 款 number.
                if len(parts) == 2:
                    matches.append(clause.path)
        elif mu is not None and len(parts) == 3 and _int_eq(parts[2], mu):
            matches.append(clause.path)
    unique = set(matches)
    if len(unique) == 1:
        return unique.pop()
    return None


def _intra_path(match: re.Match[str], ctx: LawResolveContext, article_id: int | None) -> str | None:
    if match.group("item") and match.group("kuan"):
        return _clause_path_for(
            ctx,
            article_id,
            item=_simple_int(match.group("item")),
            kuan=_simple_int(match.group("kuan")),
            mu=None,
        )
    if match.group("kuan2") and match.group("mu"):
        return _clause_path_for(
            ctx,
            article_id,
            item=None,
            kuan=_simple_int(match.group("kuan2")),
            mu=_simple_int(match.group("mu")),
        )
    if match.group("item_only"):
        return _clause_path_for(
            ctx,
            article_id,
            item=_simple_int(match.group("item_only")),
            kuan=None,
            mu=None,
        )
    if match.group("kuan_only"):
        return _clause_path_for(
            ctx,
            article_id,
            item=None,
            kuan=_simple_int(match.group("kuan_only")),
            mu=None,
        )
    if match.group("mu_only"):
        return _clause_path_for(
            ctx,
            article_id,
            item=None,
            kuan=None,
            mu=_simple_int(match.group("mu_only")),
        )
    return None


def _path_item(path: str) -> int | None:
    head = path.split("/", 1)[0]
    return int(head) if head.isdigit() else None


def _previous_sibling(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) < 2:
        return None
    try:
        number = int(parts[-1])
    except ValueError:
        return None
    if number <= 1:
        return None
    parts[-1] = str(number - 1)
    return "/".join(parts)


def _int_eq(text: str, value: int) -> bool:
    return text.isdigit() and int(text) == value


def _overlaps(masks: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(not (end <= left or start >= right) for left, right in masks)


def _looks_like_law(name: str) -> bool:
    return len(name) >= 2 and name.endswith(_LAW_SUFFIXES)
