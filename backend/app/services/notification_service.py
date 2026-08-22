"""通知服务（通知中心 SSE）：统一写入入口，异常不抛出（不阻塞主流程）。

- 落库（notifications 表）+ 广播到该角色所有在线 SSE 连接（单 worker 实时推送）；
- 多 worker / 队列丢失兜底：DB 已落库，SSE 断线重连后前端重拉列表 + 轮询角标兜底；
- 任何异常仅 ``logging.warning`` + rollback，绝不抛出（埋点零侵入，对齐 audit_service）。

M1（外部审查 2026-08-22）重写：订阅模型从"单队列抢占式消费"（一条通知被任一连接
get 走、角色不匹配即丢弃——双开标签页/多客服在线时互相偷事件）改为**每连接独立
asyncio.Queue + 按角色广播**；发布方可能在任意线程（同步端点跑在线程池），经
``loop.call_soon_threadsafe`` 投递，同时消灭了旧实现"每连接占死一个线程池线程"的问题。
"""
from __future__ import annotations

import asyncio
import logging
import threading

from sqlalchemy.orm import Session

from app.models.notification import Notification

logger = logging.getLogger(__name__)

#: 订阅表：role → 该角色所有在线 SSE 连接的 (专属队列, 所属事件循环)
_subscribers: dict[str, set[tuple[asyncio.Queue, asyncio.AbstractEventLoop]]] = {}
_sub_lock = threading.Lock()
#: 单连接积压上限：满则丢（DB 已落库，前端可重拉兜底）
_SUB_QUEUE_MAX = 200


def subscribe(role: str) -> tuple[asyncio.Queue, asyncio.AbstractEventLoop]:
    """注册一个该角色的 SSE 连接（必须在事件循环内调用）；返回 (队列, 循环) 供消费/注销。"""
    pair = (asyncio.Queue(maxsize=_SUB_QUEUE_MAX), asyncio.get_running_loop())
    with _sub_lock:
        _subscribers.setdefault(role, set()).add(pair)
    return pair


def unsubscribe(role: str, pair: tuple[asyncio.Queue, asyncio.AbstractEventLoop]) -> None:
    """注销连接（SSE 生成器 finally 调用，防泄漏）。"""
    with _sub_lock:
        _subscribers.get(role, set()).discard(pair)


def _drop_put(q: asyncio.Queue, item: dict) -> None:
    """事件循环内投递；队列满丢弃（DB 已落库兜底，不阻塞发布方）。"""
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        logger.warning("notification: 订阅队列满，丢弃实时推送（前端重拉兜底）")


def create_notification(
    db: Session,
    recipient_role: str,
    event_type: str,
    title: str,
    content: str = "",
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> Notification | None:
    """写入一条通知（落库 + 广播）；任何异常仅告警 + 回滚（埋点零侵入）。"""
    try:
        n = Notification(
            recipient_role=recipient_role,
            event_type=event_type,
            title=title,
            content=content,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        db.add(n)
        db.commit()
        db.refresh(n)
        _enqueue(n)
        return n
    except Exception:  # noqa: BLE001 - 通知失败不阻断主流程
        db.rollback()
        logger.warning(
            "通知写入失败（已忽略，不阻塞主流程）: event=%s role=%s",
            event_type,
            recipient_role,
        )
        return None


def _enqueue(n: Notification) -> None:
    """按角色广播到所有在线连接（任意线程可调；目标循环已关闭则跳过该连接）。"""
    item = {
        "recipient_role": n.recipient_role,
        "notification_id": str(n.id),
        "event_type": n.event_type,
        "title": n.title,
        "content": n.content,
        "resource_type": n.resource_type,
        "resource_id": n.resource_id,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }
    with _sub_lock:
        targets = list(_subscribers.get(n.recipient_role, ()))
    for q, loop in targets:
        try:
            loop.call_soon_threadsafe(_drop_put, q, item)
        except RuntimeError:
            pass  # 目标事件循环已关闭（连接先亡），下一次 subscribe/unsubscribe 会清掉
