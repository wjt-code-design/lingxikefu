"""smoke_import 文档清单审计（2026-08-28 KB 污染事故防线）。"""
from scripts.smoke_import import check_doc_set


def test_check_doc_set_flags_pollution():
    extra = check_doc_set({"退换货政策.md", "模拟订单-物流轨迹.md"}, {"退换货政策.md"})
    assert extra == ["模拟订单-物流轨迹.md"]


def test_check_doc_set_clean_kb_passes():
    assert check_doc_set({"退换货政策.md", "隐私政策.md"}, {"退换货政策.md", "隐私政策.md"}) == []


def test_check_doc_set_extra_docs_sorted():
    extra = check_doc_set({"b.md", "a.md", "c.md"}, {"c.md"})
    assert extra == ["a.md", "b.md"]
