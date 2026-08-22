"""通知中心（SSE）路由：/api/v1/notifications 列表/已读 + /stream 长连接推送。

- 权限：agent/admin（user 无通知）；列表/已读按 recipient_role 过滤（仅看自己角色的通知，防越权）；
- SSE：进程内队列实时推送（单 worker）+ 心跳保活；断线由前端 EventSource 自动重连 + 重拉列表兜底；
- 契约见《通知中心SSE-产品契约-2026-08-18.md》。
"""
from __future__ import annotations

import asyncio
import json
import queue
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.notification import Notification
from app.schemas.notification import NotificationItem, NotificationListResp, UnreadCountResp
from app.services.notification_service import _notify_queue

router = APIRouter(prefix="/notifications", tags=["notifications"])

#: 可接收通知的角色（user 无通知中心）
_NOTIFY_ROLES = ("agent", "admin")
#: SSE 心跳间隔（秒）：防代理/网关静默断连
_HEARTBEAT_INTERVAL = 15


class OkResp(BaseModel):
    ok: bool = True


def _item(n: Notification) -> NotificationItem:
    return NotificationItem(
        notification_id=str(n.id),
        event_type=n.event_type,
        title=n.title,
        content=n.content,
        resource_type=n.resource_type,
        resource_id=n.resource_id,
        is_read=n.is_read,
        created_at=n.created_at.isoformat() if n.created_at else "",
    )


def _require_notify_role(payload: dict) -> str:
    role = payload.get("role")
    if role not in _NOTIFY_ROLES:
        raise HTTPException(status_code=403, detail="agent/admin role required")
    return role


@router.get("", response_model=NotificationListResp)
def list_notifications(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> NotificationListResp:
    """当前角色通知列表：未读在前，按时间倒序（角标/面板数据源）。"""
    role = _require_notify_role(payload)
    cond = [Notification.tenant_id == settings.TENANT_DEFAULT, Notification.recipient_role == role]
    total = db.scalar(select(func.count(Notification.id)).where(*cond)) or 0
    rows = db.scalars(
        select(Notification)
        .where(*cond)
        .order_by(Notification.is_read.asc(), Notification.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return NotificationListResp(items=[_item(n) for n in rows], total=total)


@router.get("/unread-count", response_model=UnreadCountResp)
def unread_count(
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> UnreadCountResp:
    """当前角色未读数（角标轮询兜底；SSE 实时推送为主）。"""
    role = _require_notify_role(payload)
    cnt = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.tenant_id == settings.TENANT_DEFAULT,
            Notification.recipient_role == role,
            Notification.is_read.is_(False),
        )
    ) or 0
    return UnreadCountResp(count=cnt)


@router.post("/{notification_id}/read", response_model=OkResp)
def mark_read(
    notification_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """单条标记已读（仅本人角色可操作，防越权读他人通知）。"""
    role = _require_notify_role(payload)
    n = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.tenant_id == settings.TENANT_DEFAULT,
            Notification.recipient_role == role,
        )
    )
    if not n:
        raise HTTPException(status_code=404, detail="notification not found")
    if not n.is_read:
        n.is_read = True
        db.commit()
    return OkResp()


@router.post("/read-all", response_model=OkResp)
def mark_all_read(
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """当前角色全部标记已读。"""
    role = _require_notify_role(payload)
    db.execute(
        update(Notification)
        .where(
            Notification.tenant_id == settings.TENANT_DEFAULT,
            Notification.recipient_role == role,
            Notification.is_read.is_(False),
        )
        .values(is_read=True)
    )
    db.commit()
    return OkResp()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse_gen(role: str):
    """SSE 事件生成器（独立函数便于单测：connected 握手 → 心跳 ping / 实时通知）。"""
    yield _sse({"event": "connected", "data": {"role": role}})
    while True:
        try:
            item = await asyncio.to_thread(_notify_queue.get, timeout=_HEARTBEAT_INTERVAL)
        except queue.Empty:
            yield _sse({"event": "ping", "data": {"ts": datetime.now(UTC).isoformat()}})
            continue
        if item.get("recipient_role") == role:
            yield _sse({"event": "notification", "data": item})


@router.get("/stream")
async def stream_notifications(
    payload: dict = Depends(get_current_user),
):
    """SSE 长连接：实时推送当前角色新通知 + 心跳保活。

    事件协议：connected（握手，data.role）/ notification（新通知，data 含完整通知字段）/ ping（心跳）。
    """
    role = _require_notify_role(payload)
    return StreamingResponse(_sse_gen(role), media_type="text/event-stream")
