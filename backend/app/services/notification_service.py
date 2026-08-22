"""通知服务（通知中心 SSE）：统一写入入口，异常不抛出（不阻塞主流程）。

- 落库（notifications 表）+ 推入进程内队列（SSE 连接消费，单 worker 实时推送）；
- 多 worker / 队列丢失兜底：DB 已落库，SSE 断线重连后前端重拉列表 + 轮询角标兜底；
- 任何异常仅 ``logging.warning`` + rollback，绝不抛出（埋点零侵入，对齐 audit_service）。
"""
from __future__ import annotations

import logging
import queue

from sqlalchemy.orm import Session

from app.models.notification import Notification

logger = logging.getLogger(__name__)

#: 进程内通知队列（SSE 消费）。单 worker 实时推送；队列满直接丢弃（DB 已落库，前端可重拉兜底）。
_notify_queue: queue.Queue = queue.Queue(maxsize=1000)


def create_notification(
    db: Session,
    recipient_role: str,
    event_type: str,
    title: str,
    content: str = "",
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> Notification | None:
    """写入一条通知（落库 + 入队）；任何异常仅告警 + 回滚（埋点零侵入）。"""
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
    """入队（SSE 实时推送）；队列满丢弃（DB 已落库，前端重拉兜底）。"""
    try:
        _notify_queue.put_nowait(
            {
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
        )
    except queue.Full:
        pass
