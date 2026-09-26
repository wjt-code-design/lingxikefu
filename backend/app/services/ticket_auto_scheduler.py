"""工单自动化后台调度器：定时扫描超时工单并自动流转。

策略：每 60 秒扫描一次，执行 auto_resolve_after_timeout + auto_close_stale。
多实例安全：每轮扫描先抢 Redis 锁（SET NX + TTL + owner token，见 _acquire_scan_lock）——
同一时刻整个集群只有一个实例扫描；本轮结束在 finally 里比对 owner 后释放
（_release_scan_lock），故下一轮不会被自己挡住（B1：只抢不放会让单实例实际周期翻倍）。
持锁实例崩溃 → 无人释放，锁随 TTL 过期，不会死锁。
Redis 不可用时跳过本轮（显式日志降级，不 crash、不碰 DB）。
"""
from __future__ import annotations

import logging
import threading
import uuid

from redis.exceptions import WatchError

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()
_scheduler_started = False

SCAN_INTERVAL_SEC = 60
#: 扫描锁。锁值 = 本轮 owner token（每轮唯一），释放时据此比对，只删自己的锁。
#: TTL 只承担**崩溃兜底**（持锁进程被强杀后锁能被回收），须大于单轮最坏耗时；
#: 这与「轮末正常释放」不矛盾——正常路径靠 DEL 立即交还，异常路径才落到 TTL。
_SCAN_LOCK_KEY = "lingxi:ticket_auto:scan_lock"
_SCAN_LOCK_TTL_SEC = 90


def _acquire_scan_lock(token: str) -> bool:
    """多实例互斥：SET NX + TTL 抢锁并写入本轮 owner token；抢不到/Redis 异常均返回 False（跳过本轮）。"""
    try:
        got = get_redis().set(_SCAN_LOCK_KEY, token, nx=True, ex=_SCAN_LOCK_TTL_SEC)
        return bool(got)
    except Exception:
        logger.exception("ticket_auto_scheduler: scan lock unavailable, skip round")
        return False


def _release_scan_lock(token: str) -> None:
    """释放本轮扫描锁：比对 owner 后才删（WATCH + MULTI 原子 CAS，Redis 侧不引入 Lua 依赖）。

    绝不能用无条件 DEL——本轮耗时若超过 TTL，锁已易主，无条件删会踢掉别的实例的锁。
    锁值不是自己的、或比对到删除之间被改写（WatchError）时什么都不做，交由 TTL 回收。
    """
    try:
        with get_redis().pipeline() as pipe:
            pipe.watch(_SCAN_LOCK_KEY)
            if pipe.get(_SCAN_LOCK_KEY) != token:
                pipe.unwatch()
                return
            pipe.multi()
            pipe.delete(_SCAN_LOCK_KEY)
            pipe.execute()
    except WatchError:
        logger.warning("ticket_auto_scheduler: scan lock changed hands during release, left to TTL")
    except Exception:
        logger.exception("ticket_auto_scheduler: scan lock release failed (TTL will reclaim)")


def start_scheduler() -> None:
    """启动后台调度线程（幂等，重复调用不重复启动）。"""
    global _scheduler_thread, _scheduler_started, _stop_event
    if _scheduler_started:
        return
    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_run_loop, name="ticket-auto-scheduler", daemon=True
    )
    _scheduler_thread.start()
    _scheduler_started = True
    logger.info("ticket_auto_scheduler: started (interval=%ds)", SCAN_INTERVAL_SEC)


def stop_scheduler() -> None:
    """停止后台调度线程（优雅关闭）。"""
    global _scheduler_started
    if not _scheduler_started:
        return
    _stop_event.set()
    _scheduler_started = False
    logger.info("ticket_auto_scheduler: stop signal sent")


def _run_loop() -> None:
    while not _stop_event.is_set():
        try:
            _scan_once()
        except Exception:
            logger.exception("ticket_auto_scheduler: scan iteration failed")
        _stop_event.wait(SCAN_INTERVAL_SEC)


def _scan_once() -> None:
    """单次扫描（多实例互斥）：超时自动 resolved + 空闲自动 closed。"""
    from app.services.ticket_automation import auto_close_stale, auto_resolve_after_timeout

    token = uuid.uuid4().hex  # 本轮 owner 标识：释放时据此比对，只删自己的锁
    if not _acquire_scan_lock(token):
        return  # 其他实例正在扫描（或 Redis 不可用），本轮让位

    try:
        # SessionLocal() 起也在 try 内：建会话就失败（PG 不可达）时锁同样要交还。
        db = SessionLocal()
        try:
            if settings.AUTO_TICKET_RESOLVE_TIMEOUT_MIN > 0:
                resolved = auto_resolve_after_timeout(db)
                if resolved:
                    logger.info(
                        "ticket_auto_scheduler: auto_resolve %d tickets", len(resolved)
                    )

            if settings.AUTO_TICKET_CLOSE_IDLE_DAYS > 0:
                closed = auto_close_stale(db)
                if closed:
                    logger.info(
                        "ticket_auto_scheduler: auto_close %d tickets", len(closed)
                    )
        except Exception:
            logger.exception("ticket_auto_scheduler: scan failed")
            db.rollback()
        finally:
            db.close()
    finally:
        # B1（审计 2026-09-27）：锁必须在轮末交还——此前全仓无释放路径，
        # 60s 间隔 < 90s TTL，持有者每第二轮被自己挡住，单实例实际周期静默变成 120s。
        _release_scan_lock(token)
