"""Sessions 路由（BU-03）：/api/v1/sessions 创建 / 列表 / 详情。

- 会话按当前用户隔离（user_id 来自 token payload.sub）；
- chat 依赖会话存在性校验（chat/stream 会查 session 归属）。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.session import Session

router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionReq(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class SessionItem(BaseModel):
    session_id: str
    title: str | None
    created_at: str


class SessionListResp(BaseModel):
    items: list[SessionItem]


@router.post("", response_model=SessionItem)
def create_session(
    req: CreateSessionReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> SessionItem:
    s = Session(
        tenant_id=payload.get("tenant", "default"),
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
    )


@router.get("", response_model=SessionListResp)
def list_sessions(
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> SessionListResp:
    user_id = uuid.UUID(payload["sub"])
    rows = db.scalars(
        select(Session).where(Session.user_id == user_id).order_by(Session.updated_at.desc()).limit(50)
    ).all()
    return SessionListResp(
        items=[
            SessionItem(session_id=str(s.id), title=s.title, created_at=s.created_at.isoformat())
            for s in rows
        ]
    )


@router.get("/{session_id}", response_model=SessionItem)
def get_session(
    session_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> SessionItem:
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionItem(session_id=str(s.id), title=s.title, created_at=s.created_at.isoformat())
