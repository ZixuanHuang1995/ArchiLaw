from twbc_rag.parse.numerals import parse_chinese_numeral


def test_place_value_numerals() -> None:
    assert parse_chinese_numeral("十一") == 11
    assert parse_chinese_numeral("十") == 10
    assert parse_chinese_numeral("十五") == 15
    assert parse_chinese_numeral("二十") == 20
    assert parse_chinese_numeral("九十七") == 97
    assert parse_chinese_numeral("一百十") == 110
    assert parse_chinese_numeral("一百一十") == 110
    assert parse_chinese_numeral("一百十四") == 114
    assert parse_chinese_numeral("一百零五") == 105
    assert parse_chinese_numeral("兩") == 2


def test_positional_roc_numerals() -> None:
    # MOJ writes 101 as 一○一 (U+25CB) and 113 as 一一三, without 十/百.
    assert parse_chinese_numeral("一○一") == 101
    assert parse_chinese_numeral("一一三") == 113
    assert parse_chinese_numeral("一一四") == 114
    assert parse_chinese_numeral("一五○") == 150
    assert parse_chinese_numeral("一○四") == 104


def test_arabic_and_rejects_garbage() -> None:
    assert parse_chinese_numeral("110") == 110
    assert parse_chinese_numeral("０８") == 8
    assert parse_chinese_numeral("") is None
    assert parse_chinese_numeral("建築") is None
