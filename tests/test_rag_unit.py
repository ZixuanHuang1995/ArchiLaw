from twbc_rag.rag.chunks import Chunk, ClauseChild, is_deleted_article
from twbc_rag.rag.citations import check_citations
from twbc_rag.rag.fusion import reciprocal_rank_fusion
from twbc_rag.rag.lookup import lookup_chunk_ids


def _chunk(pcode: str, main: int, sub: int, name: str, aliases: list[str]) -> Chunk:
    article_no = f"第 {main}-{sub} 條" if sub else f"第 {main} 條"
    return Chunk(
        chunk_id=f"{pcode}:{main}-{sub}" if sub else f"{pcode}:{main}",
        pcode=pcode,
        law_name=name,
        aliases=aliases,
        heading_path="",
        article_no=article_no,
        article_no_main=main,
        article_no_sub=sub,
        article_id=main,
        version_id=1,
        modified_date="2026-02-23",
        effective_date=None,
        effective_note="",
        official_url="https://law.moj.gov.tw/",
        full_text="本文",
        clauses=[ClauseChild(1, "1", "本文", False), ClauseChild(2, "2", "但書例外", True)],
        has_proviso=True,
        has_table=False,
        attachments=[],
    )


def test_deleted_articles_are_recognized() -> None:
    assert is_deleted_article("（刪除）")
    assert is_deleted_article("（刪除）。")
    assert not is_deleted_article("（刪除）但其他文字還在。")


def test_lookup_design_code_hyphen_article() -> None:
    design = _chunk("D0070115", 110, 1, "建築技術規則建築設計施工編", ["設計施工編"])
    other = _chunk("D0070109", 11, 0, "建築法", ["建築法"])
    found = lookup_chunk_ids("設計施工編第110-1條的防火間隔", [design, other])
    assert found == ["D0070115:110-1"]


def test_lookup_ignores_ambiguous_article_one() -> None:
    laws = [
        _chunk("D0070109", 1, 0, "建築法", ["建築法"]),
        _chunk("D0070115", 1, 0, "建築技術規則建築設計施工編", ["設計施工編"]),
    ]
    assert lookup_chunk_ids("第1條是什麼？", laws) == []
    assert lookup_chunk_ids("建築法第1條是什麼？", laws) == ["D0070109:1"]


def test_rrf_prefers_documents_ranked_by_both_lists() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert fused[0][0] == "b"


def test_citation_checker_drops_unknown_articles() -> None:
    evidence = [
        _chunk(
            "D0070115",
            55,
            0,
            "建築技術規則建築設計施工編",
            ["設計施工編", "建築技術規則建築設計施工編"],
        )
    ]
    answer = "車道寬度見【建築技術規則建築設計施工編 第55條第1項】。稅率見【建築法 第9條】。"
    cleaned, kept, dropped = check_citations(answer, evidence)
    assert kept == ["建築技術規則建築設計施工編 第 55 條第1項"]
    assert dropped == ["建築法 第9條"]
    assert "第 55 條" in cleaned
    assert "第9條" not in cleaned
