"""text_splitter 单测：分块策略 + 参数边界 + snippet 清洗（UI 审查高2）。"""
from __future__ import annotations

import pytest
from app.utils.text_splitter import clean_snippet, split_text


def test_empty_text_returns_empty():
    assert split_text("") == []
    assert split_text("   \n\n  ") == []


def test_short_text_single_chunk():
    text = "这是很短的一段话。"
    assert split_text(text, chunk_size=500) == [text]


def test_multiple_paragraphs_merged_to_limit():
    paras = [f"第{i}段内容。" + "x" * 100 for i in range(1, 5)]
    text = "\n\n".join(paras)
    chunks = split_text(text, chunk_size=250, chunk_overlap=0)
    # 每块不超过上限（除首块可能含短首段外均接近上限）
    assert all(len(c) <= 250 + 20 for c in chunks)
    assert len(chunks) >= 2
    # 所有段内容都在块中出现
    joined = "".join(chunks)
    for p in paras:
        assert p in joined


def test_long_paragraph_hard_split_with_overlap():
    para = "长" * 600
    chunks = split_text(para, chunk_size=200, chunk_overlap=30)
    # 600 字符 / 200 窗口 + 30 overlap 重入 → 4 块：0:200, 170:370, 340:540, 510:600
    assert len(chunks) == 4
    assert all(len(c) <= 200 for c in chunks)
    # overlap：后一块头部应含上一块尾部字符
    assert chunks[1].startswith("长" * 30)
    assert chunks[2].startswith("长" * 30)
    assert chunks[3].startswith("长" * 30)


def test_overlap_preserves_context_across_chunks():
    text = "甲" * 60 + "乙" * 60 + "丙" * 60
    chunks = split_text(text, chunk_size=100, chunk_overlap=20)
    # 相邻块应有 20 字符的重复尾部/头部
    assert chunks[0][-20:] == chunks[1][:20] or chunks[0][-20:] in chunks[1]


def test_markdown_headings_kept_in_chunk():
    text = "# 退换货政策\n\n七天无理由退货规则。\n\n## 质量问题\n\n质量问题退货。"
    chunks = split_text(text, chunk_size=500)
    assert len(chunks) == 1
    assert chunks[0].startswith("# 退换货政策")


def test_invalid_params_raise():
    with pytest.raises(ValueError, match="chunk_size"):
        split_text("x", chunk_size=0)
    with pytest.raises(ValueError, match="overlap"):
        split_text("x", chunk_size=10, chunk_overlap=10)
    with pytest.raises(ValueError, match="overlap"):
        split_text("x", chunk_size=10, chunk_overlap=-1)


# ---------- clean_snippet（UI 审查 2026-08-31 高2：来源面板渲染原始 Markdown） ----------


def test_clean_snippet_strips_markdown_markup():
    text = "## 五、发货提醒\n\n**重要**：拍下后 `48 小时`内发货。\n- 华东仓发中通\n- 华南仓发圆通"
    out = clean_snippet(text, limit=200)
    assert "##" not in out
    assert "**" not in out
    assert "`" not in out
    assert "发货提醒" in out
    assert "华东仓发中通" in out
    assert "华南仓发圆通" in out


def test_clean_snippet_truncates_at_sentence_boundary():
    head = "这是第一句话。"
    filler = "内容" * 40
    out = clean_snippet(head + filler + "。尾部句子。", limit=20)
    assert len(out) <= 20
    assert out.endswith("。")
    assert out.startswith(head)


def test_clean_snippet_hard_cut_without_punctuation():
    out = clean_snippet("甲" * 300, limit=50)
    assert len(out) <= 50
    assert out == "甲" * 50


def test_clean_snippet_short_text_untouched():
    assert clean_snippet("保修期 12 个月。", limit=200) == "保修期 12 个月。"
    assert clean_snippet("", limit=200) == ""
