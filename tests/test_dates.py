from datetime import date

from twbc_rag.ingest.dates import parse_moj_date


def test_western_date() -> None:
    parsed, raw = parse_moj_date("20260223")
    assert parsed == date(2026, 2, 23)
    assert raw == "20260223"


def test_roc_seven_digit_date() -> None:
    parsed, _raw = parse_moj_date("1130701")
    assert parsed == date(2024, 7, 1)


def test_sentinel_effective_date_is_not_a_real_day() -> None:
    parsed, raw = parse_moj_date("99991231")
    assert parsed is None
    assert raw == "99991231"


def test_blank_date() -> None:
    assert parse_moj_date("") == (None, "")
    assert parse_moj_date(None) == (None, "")
