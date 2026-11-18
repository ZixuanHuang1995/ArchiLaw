import json
from pathlib import Path

from twbc_rag.catalog import load_catalog
from twbc_rag.ingest.huggingface import parse_hf_jsonl
from twbc_rag.ingest.moj import parse_moj_payload, pcode_from_url
from twbc_rag.ingest.pages import parse_law_page

FIXTURES = Path(__file__).parent / "fixtures"


def test_catalog_lists_the_six_week_one_laws() -> None:
    catalog = load_catalog()
    assert catalog.pcodes == [
        "D0070109",
        "D0070114",
        "D0070115",
        "D0070116",
        "D0070117",
        "D0120029",
    ]
    design = catalog.by_pcode("D0070115")
    assert design is not None
    assert design.parent_act_pcode == "D0070109"
    assert "設計施工編" in design.aliases


def test_pcode_comes_from_the_law_url() -> None:
    assert (
        pcode_from_url("https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=D0070109") == "D0070109"
    )


def test_saved_moj_fixture_parses_without_network() -> None:
    payload = json.loads((FIXTURES / "moj_sample.json").read_text(encoding="utf-8"))
    laws = parse_moj_payload(payload)
    by_pcode = {law.pcode: law for law in laws}
    assert set(by_pcode) == {"D0070109", "D0070114"}
    assert by_pcode["D0070109"].name == "建築法"
    assert by_pcode["D0070109"].modified_date.isoformat() == "2022-05-11"
    assert by_pcode["D0070114"].source == "moj_api"
    assert any(article.article_no == "第 97 條" for article in by_pcode["D0070109"].articles)
    assert "以下簡稱本法" in by_pcode["D0070114"].articles[0].content


def test_huggingface_record_round_trip() -> None:
    laws = parse_hf_jsonl(FIXTURES / "hf_sample.jsonl", {"A0000001"})
    assert len(laws) == 1
    law = laws[0]
    assert law.pcode == "A0000001"
    assert law.name == "中華民國憲法"
    assert [article.article_no for article in law.articles] == ["第 1 條", "第 2 條"]
    assert law.articles[0].heading_path == "第 一 章 總綱"
    assert law.articles[0].content == "中華民國基於三民主義，為民有民治民享之民主共和國。"
    assert law.source == "huggingface"


def test_law_page_snippet_records_real_file_ids() -> None:
    html = (FIXTURES / "page_snippet.html").read_text(encoding="utf-8")
    assets = parse_law_page(html)
    by_id = {asset.file_id: asset for asset in assets}
    assert set(by_id) == {"0000011573", "0000018218", "0000295580"}
    figure = by_id["0000011573"]
    assert figure.article_main == 1
    assert figure.article_sub == 0
    assert figure.source_url.endswith("FileId=0000011573")
    assert "lan=" not in figure.source_url
    warning = by_id["0000295580"]
    assert warning.article_main == 116
    assert warning.article_sub == 2
    assert warning.warning is not None
    assert "無法完整呈現" in warning.warning
