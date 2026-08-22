"""调度器多实例互斥锁测试（Redis SET NX + TTL；fakeredis 由 conftest 注入）。"""
from __future__ import annotations

import app.services.ticket_auto_scheduler as sched


def _reset_lock():
    sched.get_redis().delete(sched._SCAN_LOCK_KEY)


def test_scan_lock_first_acquire_wins():
    """多实例互斥：第一个拿到锁，第二个抢不到（同轮不重复扫描）。"""
    _reset_lock()
    assert sched._acquire_scan_lock() is True
    assert sched._acquire_scan_lock() is False


def test_scan_lock_reacquire_after_expiry():
    """锁过期（TTL 到/被清）后可再次获取——持锁实例崩溃不会死锁。"""
    _reset_lock()
    assert sched._acquire_scan_lock() is True
    _reset_lock()  # 模拟 TTL 过期
    assert sched._acquire_scan_lock() is True


def test_scan_lock_redis_error_skips_round(monkeypatch):
    """Redis 异常 → 显式跳过本轮（降级有日志不静默、不 crash、不碰 DB）。"""
    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(sched, "get_redis", _boom)
    assert sched._acquire_scan_lock() is False


def test_scan_once_skips_without_lock(monkeypatch):
    """锁未获取时 _scan_once 直接返回，不触碰 DB（PG 不可用也不炸）。"""
    monkeypatch.setattr(sched, "_acquire_scan_lock", lambda: False)

    def _no_pg():
        raise AssertionError("锁未获取时不应创建 DB 会话")

    monkeypatch.setattr(sched, "SessionLocal", _no_pg)
    sched._scan_once()  # 不抛 = 正确跳过
