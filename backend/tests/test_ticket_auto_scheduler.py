"""调度器多实例互斥锁测试（Redis SET NX + TTL + owner token；fakeredis 由 conftest 注入）。"""
from __future__ import annotations

import app.services.ticket_auto_scheduler as sched
import pytest


def _reset_lock():
    sched.get_redis().delete(sched._SCAN_LOCK_KEY)


def test_scan_lock_first_acquire_wins():
    """多实例互斥：第一个拿到锁，第二个抢不到（同轮不重复扫描）。"""
    _reset_lock()
    assert sched._acquire_scan_lock("token-a") is True
    assert sched._acquire_scan_lock("token-b") is False


def test_scan_lock_reacquire_after_expiry():
    """锁过期（TTL 到/被清）后可再次获取——持锁实例崩溃不会死锁。"""
    _reset_lock()
    assert sched._acquire_scan_lock("token-a") is True
    _reset_lock()  # 模拟 TTL 过期
    assert sched._acquire_scan_lock("token-a") is True


def test_scan_lock_redis_error_skips_round(monkeypatch):
    """Redis 异常 → 显式跳过本轮（降级有日志不静默、不 crash、不碰 DB）。"""
    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(sched, "get_redis", _boom)
    assert sched._acquire_scan_lock("token-a") is False


def test_scan_once_skips_without_lock(monkeypatch):
    """锁未获取时 _scan_once 直接返回，不触碰 DB（PG 不可用也不炸）。"""
    monkeypatch.setattr(sched, "_acquire_scan_lock", lambda token: False)

    def _no_pg():
        raise AssertionError("锁未获取时不应创建 DB 会话")

    monkeypatch.setattr(sched, "SessionLocal", _no_pg)
    sched._scan_once()  # 不抛 = 正确跳过


# ---------- B1（审计 2026-09-27）：锁必须交还 ----------
# 守卫断的是**行为**「轮末锁一定被释放」，不是常量大小关系：TTL 必须大于单轮最坏耗时
# （持锁实例崩溃后靠它回收），这与正常路径的显式释放并不冲突。所以既不断 TTL < 间隔
# （那会让长扫描期间第二个实例并发进来、破坏互斥），也不单断 TTL > 间隔——今天正是
# TTL(90) > 间隔(60)，却因为从不释放而变成缺陷；审计把 TTL 改成 10 后目标子集照样绿，
# 说的就是缺行为尺子。锁正常交还后，TTL 与间隔之间不存在可静态校验的正确性关系。


class _NoDb:
    """DB 会话替身：本轮不碰真库，只记录轮次是否真的进入了扫描体。"""

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def _enter_no_op_scan(monkeypatch) -> list[int]:
    """把 _scan_once 的 DB 换成替身、两个自动化开关关掉，返回「进入扫描体」计数器。"""
    entered: list[int] = []

    def _fake_session_local() -> _NoDb:
        entered.append(1)
        return _NoDb()

    monkeypatch.setattr(sched.settings, "AUTO_TICKET_RESOLVE_TIMEOUT_MIN", 0)
    monkeypatch.setattr(sched.settings, "AUTO_TICKET_CLOSE_IDLE_DAYS", 0)
    monkeypatch.setattr(sched, "SessionLocal", _fake_session_local)
    return entered


def test_scan_once_releases_lock_at_round_end(monkeypatch):
    """B1：一轮扫描结束后锁必须交还（此前全仓无 DEL 路径 → 60s 间隔 < 90s TTL，
    持有者每第二轮被自己挡住，单实例实际扫描周期静默翻倍成 120s）。"""
    entered = _enter_no_op_scan(monkeypatch)
    _reset_lock()

    sched._scan_once()
    assert len(entered) == 1
    assert sched.get_redis().get(sched._SCAN_LOCK_KEY) is None, "轮末必须释放扫描锁"

    sched._scan_once()
    assert len(entered) == 2, "同一实例的下一轮不应被上一轮自己的锁挡住"


def test_scan_once_releases_lock_when_scan_body_raises(monkeypatch):
    """扫描体抛异常（DB 故障被内部 except 吞掉）也要交还锁：故障一轮不得让后续轮次空转到 TTL。"""
    import app.services.ticket_automation as auto

    entered = _enter_no_op_scan(monkeypatch)
    monkeypatch.setattr(sched.settings, "AUTO_TICKET_RESOLVE_TIMEOUT_MIN", 1)

    def _boom(_db):
        raise RuntimeError("db down")

    monkeypatch.setattr(auto, "auto_resolve_after_timeout", _boom)
    _reset_lock()

    sched._scan_once()  # 内部 except 消化，不外抛
    assert len(entered) == 1
    assert sched.get_redis().get(sched._SCAN_LOCK_KEY) is None


def test_scan_once_releases_lock_when_session_creation_fails(monkeypatch):
    """建会话就失败（PG 不可达）同样交还锁——SessionLocal() 也在释放保护范围内。"""

    def _boom():
        raise RuntimeError("pg down")

    monkeypatch.setattr(sched, "SessionLocal", _boom)
    _reset_lock()

    with pytest.raises(RuntimeError):
        sched._scan_once()  # 异常照旧外抛（由 _run_loop 记日志），但锁已交还
    assert sched.get_redis().get(sched._SCAN_LOCK_KEY) is None


def test_scan_lock_is_held_with_ttl_during_round(monkeypatch):
    """崩溃兜底不丢：扫描进行中锁确实被持有，且带正 TTL（进程被强杀后能被回收）。"""
    entered = _enter_no_op_scan(monkeypatch)
    _reset_lock()

    def _peek_while_held() -> _NoDb:
        ttl = sched.get_redis().ttl(sched._SCAN_LOCK_KEY)
        assert ttl > 0, "扫描期间锁必须带 TTL（持锁实例崩溃后靠它回收）"
        entered.append(1)
        return _NoDb()

    monkeypatch.setattr(sched, "SessionLocal", _peek_while_held)
    sched._scan_once()


def test_release_scan_lock_only_deletes_own_lock():
    """释放按 owner 比对：锁已易主（本轮耗时超过 TTL）时不得删掉别的实例的锁。"""
    _reset_lock()
    r = sched.get_redis()
    r.set(sched._SCAN_LOCK_KEY, "someone-else", ex=sched._SCAN_LOCK_TTL_SEC)

    sched._release_scan_lock("my-token")
    assert r.get(sched._SCAN_LOCK_KEY) == "someone-else", "无条件 DEL 会踢掉其他实例的锁"

    sched._release_scan_lock("someone-else")
    assert r.get(sched._SCAN_LOCK_KEY) is None
