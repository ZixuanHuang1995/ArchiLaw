"""Chinese and arabic numerals used in Taiwan statute citations.

Two systems show up in the MOJ text:

* Place-value with 十/百/千: 十一 = 11, 一百十四 = 114, 九十七 = 97.
* Digit-by-digit, used mostly above 100: 一一三 = 113, 一○一 = 101.
  The zero glyph in the live MOJ JSON is U+25CB (○), not 零 or 〇.

If the token contains 十/百/千/萬 it is place-value. Otherwise each character
is one decimal digit. That is what distinguishes 十一 (11) from 一一 (11 in
the digit style, which these codes do not use for teens).
"""

from __future__ import annotations

import re

_DIGITS = {
    "零": 0,
    "〇": 0,
    "○": 0,
    "◯": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_UNITS = {"十": 10, "百": 100, "千": 1000, "萬": 10000, "万": 10000}
_FW_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_ARABIC = re.compile(r"[0-9]+")


def parse_chinese_numeral(token: str) -> int | None:
    """Parse a numeral token. Arabic tokens are accepted; empty/garbage returns None."""
    text = token.strip().translate(_FW_DIGITS).replace(" ", "")
    if not text:
        return None
    if _ARABIC.fullmatch(text):
        return int(text)
    if any(char in _UNITS for char in text):
        return _parse_place_value(text)
    if all(char in _DIGITS for char in text):
        value = 0
        for char in text:
            value = value * 10 + _DIGITS[char]
        return value
    return None


def _parse_place_value(text: str) -> int | None:
    total = 0
    current = 0
    seen_digit = False
    for char in text:
        if char in _DIGITS:
            current = _DIGITS[char]
            seen_digit = True
            continue
        unit = _UNITS.get(char)
        if unit is None:
            return None
        if not seen_digit:
            current = 1
        total += current * unit
        current = 0
        seen_digit = False
    return total + current
