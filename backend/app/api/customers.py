"""Customers 路由（T6）：/api/v1/customers 用户维度画像聚合（会话数/活跃度/未处理工单）。

- GET /customers?page=&size=：agent/admin 查看租户内用户画像
- 聚合：会话总数 / 最近活跃时间 / 未处理工单数（open+processing）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User

router = APIRouter(prefix="/customers", tags=["customers"])


class CustomerItem(BaseModel):
    user_id: str
    account: str
    session_count: int
    last_active: str | None
    open_tickets: int


class CustomerListResp(BaseModel):
    items: list[CustomerItem]
    total: int


@router.get("", response_model=CustomerListResp)
def list_customers(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> CustomerListResp:
    """客户画像列表（agent/admin）：用户 × 会话数 × 活跃度 × 未处理工单。"""
    if payload.get("role") not in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="agent/admin role required")
    tenant = settings.TENANT_DEFAULT

    # 未处理工单计数（按 user 聚合）：join sessions 拿 user_id
    open_ticket_cnt = (
        select(Session.user_id, func.count(Ticket.id).label("n"))
        .join(Ticket, Ticket.session_id == Session.id)
        .where(Ticket.status.in_([TicketStatus.open, TicketStatus.processing]))
        .group_by(Session.user_id)
        .subquery()
    )

    total = db.scalar(select(func.count(User.id)).where(User.tenant_id == tenant)) or 0
    rows = db.execute(
        select(
            User.id,
            User.email,
            User.phone,
            func.count(Session.id).label("session_count"),
            func.max(Session.updated_at).label("last_active"),
            func.coalesce(open_ticket_cnt.c.n, 0).label("open_tickets"),
        )
        .outerjoin(Session, Session.user_id == User.id)
        .outerjoin(open_ticket_cnt, open_ticket_cnt.c.user_id == User.id)
        .where(User.tenant_id == tenant)
        .group_by(User.id, open_ticket_cnt.c.n)
        .order_by(func.max(Session.updated_at).desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    items = [
        CustomerItem(
            user_id=str(r.id),
            account=r.email or r.phone or str(r.id),
            session_count=int(r.session_count or 0),
            last_active=r.last_active.isoformat() if r.last_active else None,
            open_tickets=int(r.open_tickets or 0),
        )
        for r in rows
    ]
    return CustomerListResp(items=items, total=total)
