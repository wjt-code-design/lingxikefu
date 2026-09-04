"""通知中心（SSE）路由：/api/v1/notifications 列表/已读 + /stream 长连接推送。

- 权限：agent/admin 看本角色通知（广播 + 定向本人）；user 仅看定向本人
  （recipient_user_id=本人，防越权读他人通知）；
- SSE：进程内队列实时推送（单 worker）+ 心跳保活；断线由前端 EventSource 自动重连 + 重拉列表兜底；
- 契约见《通知中心SSE-产品契约-2026-08-18.md》。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.notification import Notification
from app.schemas.notification import NotificationItem, NotificationListResp, UnreadCountResp
from app.services.notification_service import subscribe, unsubscribe

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


def _visibility_cond(payload: dict) -> list:
    """当前主体的通知可见性条件（按人投递，2026-09-04 D4 铃铛立项）。

    - agent/admin：本角色广播（recipient_user_id IS NULL）+ 定向本人；
    - user：仅定向本人（强过滤，杜绝读到他人/广播通知）。
    """
    role = payload.get("role")
    # sa.Uuid() 绑定要求 UUID 对象（str 会走 .hex 报错），统一转换
    uid = uuid.UUID(str(payload.get("sub")))
    base = [Notification.tenant_id == settings.TENANT_DEFAULT]
    if role in _NOTIFY_ROLES:
        return base + [
            Notification.recipient_role == role,
            or_(
                Notification.recipient_user_id.is_(None),
                Notification.recipient_user_id == uid,
            ),
        ]
    if role == "user":
        return base + [
            Notification.recipient_role == "user",
            Notification.recipient_user_id == uid,
        ]
    raise HTTPException(status_code=403, detail="no notification access for role")


@router.get("", response_model=NotificationListResp)
def list_notifications(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> NotificationListResp:
    """当前主体可见的通知列表：未读在前，按时间倒序（角标/面板数据源）。"""
    cond = _visibility_cond(payload)
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
    """当前主体未读数（角标轮询兜底；SSE 实时推送为主）。"""
    cond = _visibility_cond(payload)
    cnt = db.scalar(
        select(func.count(Notification.id)).where(*cond, Notification.is_read.is_(False))
    ) or 0
    return UnreadCountResp(count=cnt)


@router.post("/{notification_id}/read", response_model=OkResp)
def mark_read(
    notification_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """单条标记已读（仅可见范围内可操作，防越权读他人通知）。"""
    cond = _visibility_cond(payload)
    n = db.scalar(
        select(Notification).where(Notification.id == notification_id, *cond)
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
    """当前主体可见通知全部标记已读。"""
    cond = _visibility_cond(payload)
    db.execute(
        update(Notification)
        .where(*cond, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    db.commit()
    return OkResp()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse_gen(role: str, user_id: str = ""):
    """SSE 事件生成器（M1 重写 2026-08-22）：connected 握手 → 心跳 ping / 实时通知。

    每连接独立 asyncio.Queue（由 notification_service 按角色广播填充，多连接互不
    抢占），断开/中止时注销防泄漏；不再经 to_thread 阻塞取队列——消灭每连接占死
    一个线程池线程的问题（该线程池同时承担聊天链路 DB/embedding）。

    user_id：订阅者主体，定向通知只推给匹配连接（见 _enqueue）。"""
    q, _loop, _uid = subscribe(role, user_id)
    try:
        yield _sse({"event": "connected", "data": {"role": role}})
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
            except TimeoutError:  # py3.11+ asyncio.TimeoutError 即内建 TimeoutError
                yield _sse({"event": "ping", "data": {"ts": datetime.now(UTC).isoformat()}})
                continue
            yield _sse({"event": "notification", "data": item})
    finally:
        unsubscribe(role, (q, _loop, user_id))


@router.get("/stream")
async def stream_notifications(
    payload: dict = Depends(get_current_user),
):
    """SSE 长连接：实时推送当前主体可见的新通知 + 心跳保活。

    事件协议：connected（握手，data.role）/ notification（新通知，data 含完整通知字段）/ ping（心跳）。
    """
    cond_role = payload.get("role")
    if cond_role not in _NOTIFY_ROLES and cond_role != "user":
        raise HTTPException(status_code=403, detail="no notification access for role")
    return StreamingResponse(
        _sse_gen(cond_role, str(payload.get("sub", ""))), media_type="text/event-stream"
    )
