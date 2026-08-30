"""quick_answers 失效面接线测试（架构审核债 5-2）：kb_version 漂移防护。

KB 变更后快捷话术可能过时：check_kb_coverage 通过时记录通过版本（Redis 锚点跨进程 +
模块级兜底），chat 端 quick 短路前校验 is_enabled_for(kb_version)——版本不匹配即禁用走 RAG。
模块级状态用 monkeypatch 设置（自动还原），防跨测试污染。
"""
from __future__ import annotations

import logging

import pytest
from app.services import quick_answers


class _FakeRedis:
    """Redis 假对象（参照 test_answer_cache.py 的 _FakeRedis）：get/set 最小面。"""

    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None):
        self.store[k] = v


@pytest.fixture(autouse=True)
def _isolated_redis(monkeypatch):
    """逐测试隔离 Redis：默认空仓 fake（key 缺失 → 模块级回退路径）。

    不依赖本地真 Redis，也不受 conftest session 级共享 fakeredis 的跨测试污染；
    需要预置内容的用例在测试体内再次 setattr 覆盖本 fixture。raising=False：
    实现落地前（模块尚无 get_redis 属性）fixture 退化为 no-op，红测阶段既有用例不受扰。
    """
    monkeypatch.setattr(quick_answers, "get_redis", _FakeRedis, raising=False)
    monkeypatch.setattr(quick_answers, "_REDIS_WARNED", False, raising=False)


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


# ---------- Redis 持久化（架构三期 2，清 Celery 导入路径债）：跨进程门控 ----------


def test_covered_version_written_to_redis(monkeypatch):
    """覆盖检查通过且带 kb_version → 写 Redis 锚点（quick:covered_kb_version，无 TTL）。

    Celery worker 进程写入后，API/chat 进程（模块级状态为空）凭 Redis 读到通过版本：
    同版本放行、漂移版本禁用——跨进程门控生效。
    """
    fake = _FakeRedis()
    monkeypatch.setattr(quick_answers, "get_redis", lambda: fake)
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", None)
    assert quick_answers.check_kb_coverage("怎么开发票 保修多久", "7:2026-03-01") is True
    assert fake.store == {"quick:covered_kb_version": "7:2026-03-01"}

    # 模拟另一进程：模块级状态为空，仅凭 Redis 即可判定
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", None)
    assert quick_answers.is_enabled_for("7:2026-03-01") is True
    assert quick_answers.is_enabled_for("8:2026-04-01") is False


def test_redis_value_wins_over_stale_module_state(monkeypatch):
    """Redis 可达时以 Redis 为准：worker 写的新版本立即可见，模块态仅作兜底。"""
    fake = _FakeRedis()
    fake.store["quick:covered_kb_version"] = "8:2026-04-01"
    monkeypatch.setattr(quick_answers, "get_redis", lambda: fake)
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", "5:2026-01-01")  # 陈旧模块态
    assert quick_answers.is_enabled_for("8:2026-04-01") is True  # Redis 新版本放行
    assert quick_answers.is_enabled_for("7:2026-03-01") is False  # 相对 Redis 版本漂移 → 禁用


def test_redis_unavailable_falls_back_to_module_state(monkeypatch, caplog):
    """Redis 不可用 → 回退模块级状态（行为同旧版），一次性警告不随请求刷屏。"""

    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(quick_answers, "get_redis", _boom)
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", "5:2026-01-01")
    with caplog.at_level(logging.WARNING, logger="app.services.quick_answers"):
        assert quick_answers.is_enabled_for("6:2026-02-01") is False
        assert quick_answers.is_enabled_for("6:2026-02-01") is False  # 第二次请求不再警告
        assert quick_answers.is_enabled_for("5:2026-01-01") is True
    redis_warns = [r for r in caplog.records if "Redis" in r.getMessage()]
    assert len(redis_warns) == 1  # 不可用只警告一次（沿用 _WARNED_STALE_VERSION 去重模式）


def test_check_redis_write_failure_still_records_module(monkeypatch, caplog):
    """Redis 写失败 → fail-open：模块级仍记录（本进程门控照常），警告一次不刷屏。"""

    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(quick_answers, "get_redis", _boom)
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", None)
    with caplog.at_level(logging.WARNING, logger="app.services.quick_answers"):
        assert quick_answers.check_kb_coverage("怎么开发票 保修多久", "7:2026-03-01") is True
    assert quick_answers._COVERED_KB_VERSION == "7:2026-03-01"  # 双写：模块态兜底不丢
    redis_warns = [r for r in caplog.records if "Redis" in r.getMessage()]
    assert len(redis_warns) == 1


def test_no_redis_no_module_state_always_enabled(monkeypatch):
    """无 Redis（key 缺失）且无模块态 → 恒放行：与现状完全一致（向后兼容硬约束）。"""
    monkeypatch.setattr(quick_answers, "_COVERED_KB_VERSION", None)
    assert quick_answers.is_enabled_for("9:2026-05-01") is True  # 空仓 fake（key 不存在）
    assert quick_answers.is_enabled_for(None) is True

    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(quick_answers, "get_redis", _boom)
    assert quick_answers.is_enabled_for("9:2026-05-01") is True  # Redis 挂 + 无模块态 → 同现状


def test_kb_version_none_skips_redis(monkeypatch):
    """kb_version=None（无版本可比）→ 恒 True 且不触碰 Redis（chat 热路径零额外开销）。"""

    def _boom():
        raise AssertionError("kb_version=None 不应访问 Redis")

    monkeypatch.setattr(quick_answers, "get_redis", _boom)
    assert quick_answers.is_enabled_for(None) is True
