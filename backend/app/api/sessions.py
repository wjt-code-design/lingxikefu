"""Sessions 路由（BU-03）：/api/v1/sessions 创建 / 列表 / 详情。

- 会话按当前用户隔离（user_id 来自 token payload.sub）；
- chat 依赖会话存在性校验（chat/stream 会查 session 归属）。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.message import Message
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus

router = APIRouter(prefix="/sessions", tags=["sessions"])


class OkResp(BaseModel):
    ok: bool = True


class CreateSessionReq(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class SessionItem(BaseModel):
    session_id: str
    title: str | None
    created_at: str
    updated_at: str  # L2：补齐 updated_at，与前端契约对齐


class SessionMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str


class SessionDetail(BaseModel):
    """会话详情（含消息历史），供 agent 查看用户历史对话（M8）。"""
    id: str
    title: str | None
    messages: list[SessionMessage]


class SessionListResp(BaseModel):
    items: list[SessionItem]
    total: int  # L2：真实总数（非 items.length 冒充）


@router.post("", response_model=SessionItem)
def create_session(
    req: CreateSessionReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> SessionItem:
    s = Session(
        tenant_id=settings.TENANT_DEFAULT,
        user_id=uuid.UUID(payload["sub"]),
        title=req.title,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return SessionItem(
        session_id=str(s.id),
        title=s.title,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
    )


@router.get("", response_model=SessionListResp)
def list_sessions(
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> SessionListResp:
    user_id = uuid.UUID(payload["sub"])
    total = db.scalar(select(func.count(Session.id)).where(Session.user_id == user_id)) or 0
    rows = db.scalars(
        select(Session).where(Session.user_id == user_id).order_by(Session.updated_at.desc()).limit(50)
    ).all()
    return SessionListResp(
        total=total,
        items=[
            SessionItem(
                session_id=str(s.id),
                title=s.title,
                created_at=s.created_at.isoformat(),
                updated_at=s.updated_at.isoformat(),
            )
            for s in rows
        ],
    )


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> SessionDetail:
    user_id = uuid.UUID(payload["sub"])
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    # M3 + R-1：越权读防护 —— 仅会话所有者可访问；
    # agent/admin 可读任意用户会话（客服查看用户历史对话场景，M8）。
    role = payload.get("role")
    if s.user_id != user_id and role not in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="forbidden")
    msgs = db.scalars(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
    ).all()
    return SessionDetail(
        id=str(s.id),
        title=s.title,
        messages=[
            SessionMessage(
                id=str(m.id),
                role=m.role.value,
                content=m.content,
                created_at=m.created_at.isoformat(),
            )
            for m in msgs
        ],
    )


@router.delete("/{session_id}", response_model=OkResp)
def delete_session(
    session_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """删除会话（T4）：所有者或 admin；含未关闭工单的会话禁删（业务约束，防工单丢失溯源）。"""
    user_id = uuid.UUID(payload["sub"])
    role = payload.get("role")
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if s.user_id != user_id and role != "admin":
        raise HTTPException(status_code=404, detail="session not found")  # 防探测
    # 业务约束：含 open/processing 工单的会话禁删（工单需先流转关闭）
    active_ticket = db.scalar(
        select(Ticket).where(
            Ticket.session_id == session_id,
            Ticket.status.in_([TicketStatus.open, TicketStatus.processing]),
        )
    )
    if active_ticket:
        raise HTTPException(status_code=409, detail="会话存在未关闭工单，请先处理工单")
    db.delete(s)
    db.commit()
    return OkResp()


class SatisfactionReq(BaseModel):
    rating: str = Field(..., pattern="^(satisfied|neutral|unsatisfied)$")


@router.post("/{session_id}/satisfaction", response_model=OkResp)
def rate_satisfaction(
    session_id: uuid.UUID,
    body: SatisfactionReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """会话级满意度（P2-2）：user 对整段会话评分（幂等覆盖，仅限自己的会话）。"""
    user_id = uuid.UUID(payload["sub"])
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s or s.user_id != user_id:
        raise HTTPException(status_code=404, detail="session not found")  # 防探测
    s.satisfaction = body.rating
    db.commit()
    return OkResp()
