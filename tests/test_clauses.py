from twbc_rag.parse.clauses import split_clauses

# 建築法 §3, copied from the MOJ JSON (newlines already normalized).
BUILDING_ACT_ARTICLE_3 = (
    "本法適用地區如左：\n"
    "一、實施都市計畫地區。\n"
    "二、實施區域計畫地區。\n"
    "三、經內政部指定地區。\n"
    "前項地區外供公眾使用及公有建築物，本法亦適用之。\n"
    "第一項第二款之適用範圍、申請建築之審查許可、施工管理及使用管理等事項之辦法，由中央主管建築機關定之。"
)

# 建築法 §9 第二款. The 但書 is a sentence inside the 款, not a new 款.
BUILDING_ACT_ARTICLE_9_KUAN = (
    "本法所稱建造，係指左列行為：\n"
    "一、新建：為新建造之建築物或將原建築物全部拆除而重行建築者。\n"
    "二、增建：於原建築物增加其面積或高度者。但以過廊與原建築物連接者，應視為新建。\n"
    "三、改建：將建築物之一部分拆除，於原建築基地範圍內改造，而不增高或擴大面積者。"
)

# Box-drawing tables contain （一） that are cell text, not 目.
TABLE_KUAN = "一、基地臨接道路，並依左表計算之：\n┌────────┐\n│（一）基地依表列選擇。│\n└────────┘"

HEIGHT_WITH_MU = (
    "九、建築物高度：自基地地面計量至建築物最高部分之垂直高度。"
    "但屋頂突出物之高度應依下列規定：\n"
    "（一）樓梯間高度在六公尺以內。\n"
    "（二）水箱高度在二點五公尺以內。"
)


def test_item_and_kuan_follow_moj_line_breaks() -> None:
    clauses = split_clauses(BUILDING_ACT_ARTICLE_3)
    assert [(clause.path, clause.is_proviso) for clause in clauses] == [
        ("1", False),
        ("1/1", False),
        ("1/2", False),
        ("1/3", False),
        ("2", False),
        ("3", False),
    ]
    assert clauses[0].text.startswith("本法適用地區")
    assert clauses[1].text.startswith("一、實施都市計畫地區")
    assert clauses[4].text.startswith("前項地區外")


def test_proviso_is_split_out_of_its_kuan() -> None:
    clauses = split_clauses(BUILDING_ACT_ARTICLE_9_KUAN)
    proviso = [clause for clause in clauses if clause.is_proviso]
    assert len(proviso) == 1
    assert proviso[0].path == "1/2"
    assert proviso[0].text.startswith("但以過廊")
    main = next(clause for clause in clauses if clause.path == "1/2" and not clause.is_proviso)
    assert "但" not in main.text
    assert main.text.endswith("高度者。")


def test_table_cells_are_not_parsed_as_mu() -> None:
    clauses = split_clauses(TABLE_KUAN)
    assert [clause.path for clause in clauses] == ["1/1"]
    assert "（一）基地依表列選擇" in clauses[0].text
    assert "┌" in clauses[0].text
    assert clauses[0].is_proviso is False


def test_mu_under_a_kuan_and_its_proviso() -> None:
    clauses = split_clauses(HEIGHT_WITH_MU)
    assert [(clause.path, clause.is_proviso) for clause in clauses] == [
        ("1/9", False),
        ("1/9", True),
        ("1/9/1", False),
        ("1/9/2", False),
    ]
    assert clauses[1].text.startswith("但屋頂突出物")
    assert clauses[2].text.startswith("（一）")


def test_deleted_article_is_one_clause() -> None:
    clauses = split_clauses("（刪除）")
    assert len(clauses) == 1
    assert clauses[0].path == "1"
    assert clauses[0].text == "（刪除）"
