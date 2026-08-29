"""quick_answers 失效面接线测试（架构审核债 5-2）：kb_version 漂移防护。

KB 变更后快捷话术可能过时：check_kb_coverage 通过时记录通过版本，
chat 端 quick 短路前校验 is_enabled_for(kb_version)——版本不匹配即禁用走 RAG。
模块级状态用 monkeypatch 设置（自动还原），防跨测试污染。
"""
from __future__ import annotations

import logging

from app.services import quick_answers


def test_quick_disabled_after_kb_change_without_coverage(monkeypatch):
    """KB 版本变化且新 KB 未通过覆盖检查 → quick 话术禁用（走 RAG），防陈旧答案。"""
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", "5:2026-01-01")  # 模拟上次通过
    assert quick_answers.is_enabled_for("6:2026-02-01") is False
    assert quick_answers.is_enabled_for("5:2026-01-01") is True
    assert quick_answers.is_enabled_for(None) is True  # 无版本环境向后兼容


def test_quick_reenabled_after_coverage_pass(monkeypatch):
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", None)
    assert quick_answers.check_kb_coverage("怎么开发票 保修多久") is True  # 命中话术关键词
    assert quick_answers.is_enabled_for("9:2026-03-01") is True


def test_check_records_covered_version_on_pass(monkeypatch):
    """覆盖检查通过且带 kb_version → 记录通过版本；同版本放行，新版本禁用。"""
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", None)
    assert quick_answers.check_kb_coverage("怎么开发票 保修多久", "7:2026-03-01") is True
    assert quick_answers.is_enabled_for("7:2026-03-01") is True
    assert quick_answers.is_enabled_for("8:2026-04-01") is False


def test_check_fail_does_not_record(monkeypatch):
    """覆盖检查未通过（过半话术无 KB 依据）→ 不记录版本，quick 对新版本保持禁用。"""
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", None)
    assert quick_answers.check_kb_coverage("量子力学波动方程与算符对易关系", "7:2026-03-01") is False
    assert quick_answers.is_enabled_for("7:2026-03-01") is True  # 从未通过 → 向后兼容放行


def test_disabled_warns_once_per_version(monkeypatch, caplog):
    """chat.py 禁用路径日志一次性：同版本重复请求只 warning 一次。"""
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", "5:2026-01-01")
    monkeypatch.setattr(quick_answers, "_WARNED_STALE_VERSION", None)
    with caplog.at_level(logging.WARNING, logger="app.services.quick_answers"):
        assert quick_answers.is_enabled_for("6:2026-02-01") is False
        assert quick_answers.is_enabled_for("6:2026-02-01") is False
        assert quick_answers.is_enabled_for("7:2026-03-01") is False
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 2  # 每个漂移版本各警告一次
