"""MOJ date strings.

Western ``YYYYMMDD`` is what the current JSON API returns (``20260223``).
Older dumps use a 7-digit ROC date (``1130701`` = 2024-07-01). ``99991231``
is the API's sentinel for "not a single effective date".
"""

from __future__ import annotations

from datetime import date


def parse_moj_date(raw: str | None) -> tuple[date | None, str]:
    text = (raw or "").strip()
    if not text:
        return None, ""
    if not text.isdigit() or text.startswith("9999") or text == "00000000":
        return None, text
    try:
        if len(text) == 8:
            year, month, day = int(text[:4]), int(text[4:6]), int(text[6:8])
        elif len(text) == 7:
            year = int(text[:3]) + 1911
            month, day = int(text[3:5]), int(text[5:7])
        else:
            return None, text
        return date(year, month, day), text
    except ValueError:
        return None, text
