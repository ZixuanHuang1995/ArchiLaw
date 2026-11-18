from twbc_rag.parse.crossref import (
    ClauseView,
    LawResolveContext,
    clean_law_name,
    extract_refs_for_article,
    find_shorthand,
    parse_article_number,
)


def _context() -> LawResolveContext:
    names = {
        "建築法": "D0070109",
        "建築技術規則建築設計施工編": "D0070115",
        "建築設計施工編": "D0070115",
        "設計施工編": "D0070115",
    }
    articles = {
        "D0070109": {(11, 0): 11, (97, 0): 97, (3, 0): 3},
        "D0070115": {(1, 0): 1, (110, 1): 1101, (113, 0): 113, (114, 0): 114},
    }
    clauses = {
        1: [
            ClauseView(1, "1", False, "chapeau"),
            ClauseView(2, "1/10", False, "十、屋頂突出物"),
            ClauseView(3, "1/10/1", False, "（一）樓梯間"),
        ],
        113: [ClauseView(10, "1/8", False, "八、用途")],
    }
    return LawResolveContext(
        pcode="D0070115",
        name="建築技術規則建築設計施工編",
        benfa_pcode="D0070109",
        benfa_name="建築法",
        name_to_pcode=names,
        articles_by_pcode=articles,
        clauses_by_article=clauses,
        ordered_keys=[(1, 0), (110, 1), (113, 0), (114, 0)],
        known_names=sorted(names, key=len, reverse=True),
    )


def _only(text: str, *, path: str = "1", key: tuple[int, int] = (1, 0)):
    hits = extract_refs_for_article([ClauseView(1, path, False, text)], _context(), key)
    assert hits, text
    return hits


def test_article_number_forms() -> None:
    assert parse_article_number("一百十", "一") == (110, 1)
    assert parse_article_number("110-1", None) == (110, 1)
    assert parse_article_number("一一四", None) == (114, 0)
    assert parse_article_number("11", None) == (11, 0)


def test_benfa_and_explicit_law_name_resolve() -> None:
    benfa = _only("本法第十一條所稱一宗土地")[0]
    assert (benfa.target_pcode, benfa.target_main, benfa.target_sub) == ("D0070109", 11, 0)
    assert "本法" in benfa.raw_span

    explicit = _only("適用建築法第 11 條")[0]
    assert (explicit.target_pcode, explicit.target_main) == ("D0070109", 11)

    shorthand = _only("本規則依建築法（以下簡稱本法）第九十七條規定訂之。")[0]
    assert (shorthand.target_pcode, shorthand.target_main, shorthand.target_sub) == (
        "D0070109",
        97,
        0,
    )
    assert find_shorthand("建築法（以下簡稱本法）")["本法"] == "建築法"


def test_positional_and_hyphen_article_numbers_resolve() -> None:
    positional = _only("供本編第一一四條第一款規定")[0]
    assert (positional.target_pcode, positional.target_main, positional.target_sub) == (
        "D0070115",
        114,
        0,
    )
    hyphen = _only("見第 110-1 條")[0]
    assert (hyphen.target_main, hyphen.target_sub) == (110, 1)
    classic = _only("第一百十條之一")[0]
    assert (classic.target_main, classic.target_sub) == (110, 1)


def test_kuan_on_another_article_and_unknown_law() -> None:
    cited = _only("供本編第一一三條第八款使用")[0]
    assert (cited.target_pcode, cited.target_main) == ("D0070115", 113)
    assert cited.target_clause_path == "1/8"

    unknown = _only("依都市計畫法第三十二條")[0]
    assert unknown.target_pcode is None
    assert unknown.target_law_name == "都市計畫法"


def test_relative_previous_item_and_previous_article() -> None:
    previous_item = _only("前項地區外供公眾使用。", path="2", key=(1, 0))[0]
    assert previous_item.target_pcode == "D0070115"
    assert (previous_item.target_main, previous_item.target_sub) == (1, 0)
    assert previous_item.target_clause_path == "1"

    previous_article = _only("依前條辦理。", key=(110, 1))[0]
    assert (previous_article.target_main, previous_article.target_sub) == (1, 0)


def test_clean_law_name_does_not_slice_titles() -> None:
    assert clean_law_name("本規則依建築法") == "建築法"
    assert clean_law_name("建築法") == "建築法"
    assert clean_law_name("違反都市計畫法") == "都市計畫法"
    assert clean_law_name("國有財產法") == "國有財產法"
