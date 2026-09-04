"""工单自动化后台调度器：定时扫描超时工单并自动流转。

策略：每 60 秒扫描一次，执行 auto_resolve_after_timeout + auto_close_stale。
多实例安全：每轮扫描先抢 Redis 锁（SET NX + TTL，见 _acquire_scan_lock）——
同一时刻整个集群只有一个实例扫描；持锁实例崩溃后锁随 TTL 过期，不会死锁。
Redis 不可用时跳过本轮（显式日志降级，不 crash、不碰 DB）。
"""
from __future__ import annotations

import logging
import threading

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()
_scheduler_started = False

SCAN_INTERVAL_SEC = 60
#: 扫描锁：TTL 略大于扫描间隔——持锁者崩溃后锁自动过期，无需显式释放
_SCAN_LOCK_KEY = "lingxi:ticket_auto:scan_lock"
_SCAN_LOCK_TTL_SEC = 90


def _acquire_scan_lock() -> bool:
    """多实例互斥：SET NX + TTL 抢锁；抢不到/Redis 异常均返回 False（跳过本轮）。"""
    try:
        got = get_redis().set(_SCAN_LOCK_KEY, "1", nx=True, ex=_SCAN_LOCK_TTL_SEC)
        return bool(got)
    except Exception:
        logger.exception("ticket_auto_scheduler: scan lock unavailable, skip round")
        return False


#: guest 留存清理日锁：TTL 24h——扫描循环每 60s 尝试一次，抢到即执行，
#: 全天其余轮次自然跳过；持锁实例崩溃后锁过期，下轮别的实例补跑（purge 幂等）
_GUEST_PURGE_LOCK_KEY = "lingxi:guest_purge:lock"
_GUEST_PURGE_LOCK_TTL_SEC = 24 * 3600


def _acquire_guest_purge_lock() -> bool:
    try:
        return bool(get_redis().set(_GUEST_PURGE_LOCK_KEY, "1", nx=True, ex=_GUEST_PURGE_LOCK_TTL_SEC))
    except Exception:
        logger.exception("guest purge lock unavailable, skip")
        return False


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
    """单次扫描（多实例互斥）：超时自动 resolved + 空闲自动 closed + 每日 guest 清理。"""
    from app.services.ticket_automation import auto_close_stale, auto_resolve_after_timeout

    if not _acquire_scan_lock():
        return  # 其他实例正在扫描（或 Redis 不可用），本轮让位

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

    _purge_expired_guests_once()


def _purge_expired_guests_once() -> None:
    """匿名会话留存清理（2026-09-04 批次B）：每日一次删超期 guest 行。

    与工单扫描共用线程但独立日锁（60s 轮询抢 24h 锁 = 每天执行一次）；
    GUEST_RETENTION_DAYS<=0 关闭清理（对齐 AUTO_TICKET_* 阈值语义）。
    失败仅日志、不抛出——清理缺一天不影响业务正确性。
    """
    if settings.GUEST_RETENTION_DAYS <= 0:
        return
    if not _acquire_guest_purge_lock():
        return
    from app.services.guest_service import purge_expired_guests

    db = SessionLocal()
    try:
        removed = purge_expired_guests(db)
        if removed:
            logger.info("guest_purge: removed %d expired guests", removed)
    except Exception:
        logger.exception("guest_purge: failed")
        db.rollback()
    finally:
        db.close()
