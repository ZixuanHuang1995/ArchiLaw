from twbc_rag.parse.headings import format_heading_path, update_heading_stack


def test_heading_stack_replaces_same_rank_and_keeps_parents() -> None:
    stack: list[tuple[int, str]] = []
    update_heading_stack(stack, "第 一 編 總則")
    update_heading_stack(stack, "   第 一 章 滅火設備")
    update_heading_stack(stack, "      第 一 節 滅火器")
    assert format_heading_path(stack) == "第 一 編 總則 / 第 一 章 滅火設備 / 第 一 節 滅火器"
    update_heading_stack(stack, "      第 六 節之一 鹵化烴滅火設備")
    assert (
        format_heading_path(stack)
        == "第 一 編 總則 / 第 一 章 滅火設備 / 第 六 節之一 鹵化烴滅火設備"
    )
    update_heading_stack(stack, "   第 二 章 警報設備")
    assert format_heading_path(stack) == "第 一 編 總則 / 第 二 章 警報設備"
