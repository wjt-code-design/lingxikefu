"""Tickets 路由（T1 工单闭环）：/api/v1/tickets 建单 / 列表 / 状态流转。

- POST /tickets：AI handoff 建单（chat.py 内部调用）+ agent 手动建单
  幂等：同一 session 已有 open/processing 工单时返回既有（防重复建单）
- GET /tickets?status=：agent/admin 列表（tenant 隔离 + 分页）
- PATCH /tickets/{id}：状态流转（open→processing→resolved/closed，closed 终态）+ 分配 assignee
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user, require_admin
from app.core.config import settings
from app.core.database import get_db
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.services.audit_service import audit_log
from app.services.notification_service import create_notification
from app.services.ticket_events import _sse_gen, publish_ticket_event
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])

#: 合法状态迁移（closed 为终态）
_ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.open: {TicketStatus.processing, TicketStatus.closed},
    TicketStatus.processing: {TicketStatus.resolved, TicketStatus.closed},
    TicketStatus.resolved: {TicketStatus.closed},
    TicketStatus.closed: set(),
}


def _publish_ticket_for_user(db: OrmSession, ticket: Ticket) -> None:
    """工单状态变更 → 推送给工单所属客户（尽力而为；前端轮询兜底）。"""
    sess = db.get(Session, ticket.session_id)
    if sess is not None:
        publish_ticket_event(str(sess.user_id), str(ticket.id), ticket.status.value)


class CreateTicketReq(BaseModel):
    session_id: uuid.UUID
    message_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class TicketItem(BaseModel):
    ticket_id: str
    session_id: str
    message_id: str | None
    status: str
    source: str = "ai"  # ai（LLM 自动）/ manual（用户主动转人工）
    assignee_id: str | None
    created_at: str
    updated_at: str
    version: int  # S2 乐观锁版本号：客户端流转时回传，服务端以原子条件比较防并发覆盖


class TicketListResp(BaseModel):
    items: list[TicketItem]
    total: int


class StatusUpdateReq(BaseModel):
    status: TicketStatus
    assignee_id: uuid.UUID | None = None
    version: int  # S2 乐观锁：客户端回传当前版本，与服务端不匹配返回 409


class OkResp(BaseModel):
    ok: bool = True


def _item(t: Ticket) -> TicketItem:
    return TicketItem(
        ticket_id=str(t.id),
        session_id=str(t.session_id),
        message_id=str(t.message_id) if t.message_id else None,
        status=t.status.value,
        source=t.source or "ai",
        assignee_id=str(t.assignee_id) if t.assignee_id else None,
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat(),
        version=t.version,
    )


def ensure_active_ticket(
    db: OrmSession,
    session_id: uuid.UUID,
    message_id: uuid.UUID | None = None,
    source: str = "ai",
    notify: bool = True,
) -> Ticket | None:
    """AI 建单 helper（chat.py handoff 时调用）：幂等 + fail-open。

    同 session 已有 open/processing 工单 → 返回既有（不重复建）；
    任何异常 → 回滚并返回 None（不阻断 SSE 流，chat 层降级为原话术）。
    notify=False：手动转人工时由 escalate_ticket 单独发 ticket.transfer，避免重复 ticket.created。
    """
    try:
        active = db.scalar(
            select(Ticket).where(
                Ticket.session_id == session_id,
                Ticket.status.in_([TicketStatus.open, TicketStatus.processing]),
            )
        )
        if active:
            return active
        t = Ticket(
            tenant_id=settings.TENANT_DEFAULT,
            session_id=session_id,
            message_id=message_id,
            source=source,
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        # 通知中心：AI handoff 建单成功 → 推给 agent（fail-open，不阻断问答流）
        if notify:
            create_notification(
                db,
                recipient_role="agent",
                event_type="ticket.created",
                title="新工单待处理",
                content=f"AI 转人工已建工单 {t.id}",
                resource_type="ticket",
                resource_id=str(t.id),
            )
        return t
    except Exception:  # noqa: BLE001 - fail-open：建单失败不影响问答流
        db.rollback()
        logger.exception("AI 建单失败（fail-open 降级）")
        return None


@router.post("", response_model=TicketItem, status_code=201)
def create_ticket(
    req: CreateTicketReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> TicketItem:
    """建单（agent/admin 手动；AI 建单走 chat.py 内部 helper）。幂等：同 session 有活跃工单不重复建。"""
    if payload.get("role") not in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="agent/admin role required")
    # 校验 session 存在
    s = db.scalar(select(Session).where(Session.id == req.session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")

    # 幂等：同 session 已有 open/processing 工单 → 返回既有
    active = db.scalar(
        select(Ticket).where(
            Ticket.session_id == req.session_id,
            Ticket.status.in_([TicketStatus.open, TicketStatus.processing]),
        )
    )
    if active:
        return _item(active)

    t = Ticket(
        tenant_id=settings.TENANT_DEFAULT,
        session_id=req.session_id,
        message_id=req.message_id,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    # Phase4 审计埋点：工单创建（ticket.create）
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="ticket.create",
        resource="ticket",
        resource_id=str(t.id),
    )
    # 通知中心：手动建单 → 推给 agent（fail-open）
    create_notification(
        db,
        recipient_role="agent",
        event_type="ticket.created",
        title="新工单待处理",
        content=f"客服已建工单 {t.id}",
        resource_type="ticket",
        resource_id=str(t.id),
    )
    return _item(t)


@router.get("", response_model=TicketListResp)
def list_tickets(
    status: TicketStatus | None = Query(default=None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> TicketListResp:
    """工单列表（agent/admin）。"""
    if payload.get("role") not in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="agent/admin role required")
    tenant = settings.TENANT_DEFAULT
    cond = [Ticket.tenant_id == tenant]
    if status:
        cond.append(Ticket.status == status)
    total = db.scalar(select(func.count(Ticket.id)).where(*cond)) or 0
    rows = (
        db.scalars(select(Ticket).where(*cond).order_by(Ticket.updated_at.desc()).offset((page - 1) * size).limit(size))
        .all()
    )
    return TicketListResp(items=[_item(t) for t in rows], total=total)


@router.get("/my", response_model=TicketListResp)
def list_my_tickets(
    status: TicketStatus | None = Query(default=None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> TicketListResp:
    """我的工单（P2-1，user 可调）：按当前用户会话归属过滤（Ticket→Session.user_id）。"""
    uid = uuid.UUID(payload["sub"])
    cond = [
        Ticket.tenant_id == settings.TENANT_DEFAULT,
        Session.user_id == uid,
    ]
    if status:
        cond.append(Ticket.status == status)
    total = (
        db.scalar(
            select(func.count(Ticket.id))
            .join(Session, Ticket.session_id == Session.id)
            .where(*cond)
        )
        or 0
    )
    rows = (
        db.scalars(
            select(Ticket)
            .join(Session, Ticket.session_id == Session.id)
            .where(*cond)
            .order_by(Ticket.updated_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .all()
    )
    return TicketListResp(items=[_item(t) for t in rows], total=total)


@router.get("/my/stream")
async def my_ticket_stream(
    payload: dict = Depends(get_current_user),
):
    """用户侧工单状态 SSE（第6组项4）：实时推送本人工单状态变更 + 心跳。

    事件协议：connected（握手，data.user_id）/ ticket_update（data: user_id/ticket_id/status/ts）/ ping（心跳）。
    尽力而为（单 worker 进程内分发）；前端保留 30s 轮询兜底，保证断线/多 worker 下最终一致。
    """
    user_id = str(payload["sub"])
    return StreamingResponse(_sse_gen(user_id), media_type="text/event-stream")


@router.patch("/{ticket_id}", response_model=TicketItem)
def update_ticket(
    ticket_id: uuid.UUID,
    req: StatusUpdateReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> TicketItem:
    """状态流转 + 分配（agent/admin）；校验合法迁移 + S2 乐观锁（version 原子比较，冲突 409）。

    并发双客服操作同一工单时，以 ``UPDATE ... WHERE version=req.version`` 原子比较：
    后提交方 rowcount=0 → 409（已被人更新），杜绝「后者静默覆盖」的审计与实况不一致。
    """
    if payload.get("role") not in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="agent/admin role required")
    t = db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.tenant_id == settings.TENANT_DEFAULT))
    if not t:
        raise HTTPException(status_code=404, detail="ticket not found")
    # 迁移合法性校验（读当前状态，不提交）
    cur = t.status
    if req.status != cur and req.status not in _ALLOWED_TRANSITIONS.get(cur, set()):
        raise HTTPException(status_code=400, detail=f"非法状态迁移: {cur.value} -> {req.status.value}")
    # S2 乐观锁：原子 UPDATE ... WHERE version = 客户端回传版本
    values: dict = {}
    if req.status != cur:
        values["status"] = req.status
    if req.assignee_id is not None:
        values["assignee_id"] = req.assignee_id
    if not values:
        # 无实际变更（同状态且未传 assignee）：仅校验版本一致，直接返回当前
        if t.version != req.version:
            raise HTTPException(status_code=409, detail="工单已被其他客服更新，请刷新后重试")
        return _item(t)
    res = db.execute(
        update(Ticket)
        .where(
            Ticket.id == ticket_id,
            Ticket.tenant_id == settings.TENANT_DEFAULT,
            Ticket.version == req.version,
        )
        .values(**values, version=Ticket.version + 1)
    )
    if res.rowcount == 0:
        db.rollback()
        raise HTTPException(status_code=409, detail="工单已被其他客服更新，请刷新后重试")
    db.commit()
    db.refresh(t)
    # Phase4 审计埋点：工单状态变更（ticket.update，detail=新状态）
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="ticket.update",
        resource="ticket",
        resource_id=str(ticket_id),
        detail=str(t.status.value),
    )
    _publish_ticket_for_user(db, t)  # 状态流转 → 实时推送给客户
    return _item(t)


@router.delete("/{ticket_id}", response_model=OkResp)
def delete_ticket(
    ticket_id: uuid.UUID,
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """删除工单（仅 admin，误建清理）。"""
    t = db.scalar(select(Ticket).where(Ticket.id == ticket_id, Ticket.tenant_id == settings.TENANT_DEFAULT))
    if not t:
        raise HTTPException(status_code=404, detail="ticket not found")
    db.delete(t)
    db.commit()
    return OkResp()


@router.post("/escalate/{session_id}", response_model=TicketItem, status_code=201)
def escalate_ticket(
    session_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> TicketItem:
    """用户主动转人工（P0-4）：把当前会话升级为工单。

    - 权限：任意登录用户（仅限自己的会话，越权 403）；
    - 幂等：复用 ensure_active_ticket（同 session 已有 open/processing 工单 → 返回既有）；
    - 显式失败：区别于 AI handoff 的 fail-open，手动转人工失败必须 503 提示（用户可感知）。
    """
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if str(s.user_id) != payload["sub"]:
        raise HTTPException(status_code=403, detail="not your session")
    t = ensure_active_ticket(db, session_id, source="manual", notify=False)
    if t is None:
        raise HTTPException(status_code=503, detail="工单创建失败，请稍后重试")
    # 通知中心：用户主动转人工 → 推给 agent（fail-open）
    create_notification(
        db,
        recipient_role="agent",
        event_type="ticket.transfer",
        title="用户转人工",
        content=f"会话 {session_id} 用户请求人工服务",
        resource_type="ticket",
        resource_id=str(t.id),
    )
    _publish_ticket_for_user(db, t)  # 转人工建单 → 实时推送给客户
    return _item(t)
