"""Tickets 路由（T1 工单闭环）：/api/v1/tickets 建单 / 列表 / 状态流转。

- POST /tickets：AI handoff 建单（chat.py 内部调用）+ agent 手动建单
  幂等：同一 session 已有 open/processing 工单时返回既有（防重复建单）
- GET /tickets?status=：agent/admin 列表（tenant 隔离 + 分页）
- PATCH /tickets/{id}：状态流转（open→processing→resolved/closed，closed 终态）+ 分配 assignee
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user, require_admin, require_roles
from app.core.config import settings
from app.core.database import get_db
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.services.audit_service import audit_log
from app.services.notification_service import create_notification
from app.services.ticket_service import ensure_active_ticket, notify_ticket_owner
from app.services.ticket_state_machine import timestamp_field_for

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets", tags=["tickets"])

#: 合法状态迁移（closed 为终态）
_ALLOWED_TRANSITIONS: dict[TicketStatus, set[TicketStatus]] = {
    TicketStatus.open: {TicketStatus.processing, TicketStatus.closed},
    TicketStatus.processing: {TicketStatus.resolved, TicketStatus.closed},
    TicketStatus.resolved: {TicketStatus.closed},
    TicketStatus.closed: set(),
}


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
    # T3 遗留补发（架构二期 1）：移交摘要（build_handoff_summary 产物的 JSON 文本）——
    # 一期已落库但只写不下发，坐席端此前拿不到
    summary: str | None = None
    # L2 预起草（架构二期 1）：AI 预草拟回复 + 种类（ai=AI 预起草）；未起草 NULL
    draft_suggestion: str | None = None
    draft_kind: str | None = None
    # 一期 4 时间戳补发：逐状态流转时间（此前只写不下发；closed 无独立列，用 updated_at）
    processing_at: str | None = None
    resolved_at: str | None = None
    # UI 审查低19：关联会话主题（列表页"主题"列数据源；仅 list 端点填充，get 单查不冗余 join）
    session_title: str | None = None


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
        summary=t.summary,
        draft_suggestion=t.draft_suggestion,
        draft_kind=t.draft_kind,
        processing_at=t.processing_at.isoformat() if t.processing_at else None,
        resolved_at=t.resolved_at.isoformat() if t.resolved_at else None,
    )


@router.post("", response_model=TicketItem, status_code=201)
def create_ticket(
    req: CreateTicketReq,
    payload: dict = Depends(require_roles("admin", "agent")),
    db: OrmSession = Depends(get_db),
) -> TicketItem:
    """建单（agent/admin 手动；AI 建单走 chat.py 内部 helper）。幂等：同 session 有活跃工单不重复建。"""
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
    keyword: str | None = Query(None, max_length=100, description="工单号/会话号搜索（去横线归一化，支持 8 位短号）"),
    payload: dict = Depends(require_roles("admin", "agent")),
    db: OrmSession = Depends(get_db),
) -> TicketListResp:
    """工单列表（agent/admin）。keyword 搜工单号/会话号（UI 审查中6）。

    归一化（code-review 修正 PG 方言）：SQL 端 replace('-','') 把 CAST 文本归一成
    无横线形态——SQLite（sa.Uuid 存 hex32）不变、PG（原生 uuid 列 CAST 出带横线
    文本）去横线——keyword 侧去横线小写后 ilike，两方言下 dashed 完整号、8 位短号、
    会话号前缀均可命中（此前仅 SQLite 测试库验证，PG 下完整号静默零结果）。
    """
    tenant = settings.TENANT_DEFAULT
    cond = [Ticket.tenant_id == tenant]
    if status:
        cond.append(Ticket.status == status)
    if keyword:
        kw = f"%{keyword.strip().lower().replace('-', '')}%"
        norm_id = func.replace(func.lower(func.cast(Ticket.id, sa.String)), "-", "")
        norm_sid = func.replace(func.lower(func.cast(Ticket.session_id, sa.String)), "-", "")
        cond.append(or_(norm_id.ilike(kw), norm_sid.ilike(kw)))
    total = db.scalar(select(func.count(Ticket.id)).where(*cond)) or 0
    rows = (
        db.scalars(select(Ticket).where(*cond).order_by(Ticket.updated_at.desc()).offset((page - 1) * size).limit(size))
        .all()
    )
    # UI 审查低19：批量取关联会话主题（避免 N+1），列表页"主题"列展示
    session_ids = {t.session_id for t in rows}
    title_map: dict[uuid.UUID, str | None] = {}
    if session_ids:
        for s in db.scalars(select(Session).where(Session.id.in_(session_ids))).all():
            title_map[s.id] = s.title
    items = []
    for t in rows:
        item = _item(t)
        item.session_title = title_map.get(t.session_id)
        items.append(item)
    return TicketListResp(items=items, total=total)


@router.get("/mine", response_model=TicketListResp)
def list_my_tickets(
    status: TicketStatus | None = Query(default=None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> TicketListResp:
    """用户侧「我的工单」列表（D2）：仅返回本人会话归属的工单。

    越权面与 get_ticket 同款口径：join Session 过滤 user_id==当前用户；
    staff 走 /tickets 管理列表，本端点不为其特设（查自己也是空集，无害）。
    必须注册在 /{ticket_id} 之前——否则 "mine" 被当 uuid 路径参数解析 422。
    """
    uid = uuid.UUID(payload["sub"])
    cond = [Ticket.tenant_id == settings.TENANT_DEFAULT, Session.user_id == uid]
    if status:
        cond.append(Ticket.status == status)
    base = select(Ticket).join(Session, Ticket.session_id == Session.id).where(*cond)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(Ticket.updated_at.desc()).offset((page - 1) * size).limit(size)).all()
    # 与 staff 列表同款批量取会话主题（防 N+1）
    session_ids = {t.session_id for t in rows}
    title_map: dict[uuid.UUID, str | None] = {}
    if session_ids:
        for s in db.scalars(select(Session).where(Session.id.in_(session_ids))).all():
            title_map[s.id] = s.title
    items = []
    for t in rows:
        item = _item(t)
        item.session_title = title_map.get(t.session_id)
        items.append(item)
    return TicketListResp(items=items, total=total)


@router.get("/{ticket_id}", response_model=TicketItem)
def get_ticket(
    ticket_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> TicketItem:
    """查询单个工单状态（聊天页角标轮询用）。

    越权防护：
    - user 仅可查自己会话归属的工单（Ticket→Session.user_id==当前用户）；
    - agent/admin 放行（客服/管理员可查看任意工单，供 observe 视角轮询）。
    """
    uid = uuid.UUID(payload["sub"])
    role = payload.get("role")
    t = db.scalar(
        select(Ticket)
        .join(Session, Ticket.session_id == Session.id)
        .where(
            Ticket.id == ticket_id,
            Ticket.tenant_id == settings.TENANT_DEFAULT,
            *([] if role in ("admin", "agent") else [Session.user_id == uid]),
        )
    )
    if not t:
        raise HTTPException(status_code=404, detail="ticket not found")
    return _item(t)


@router.patch("/{ticket_id}", response_model=TicketItem)
def update_ticket(
    ticket_id: uuid.UUID,
    req: StatusUpdateReq,
    payload: dict = Depends(require_roles("admin", "agent")),
    db: OrmSession = Depends(get_db),
) -> TicketItem:
    """状态流转 + 分配（agent/admin）；校验合法迁移 + S2 乐观锁（version 原子比较，冲突 409）。

    并发双客服操作同一工单时，以 ``UPDATE ... WHERE version=req.version`` 原子比较：
    后提交方 rowcount=0 → 409（已被人更新），杜绝「后者静默覆盖」的审计与实况不一致。
    """
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
        # 架构一期 4：状态实际变化时按目标状态补流转时间戳（与状态机同口径）
        stamp = timestamp_field_for(req.status)
        if stamp:
            values[stamp] = datetime.now(UTC)
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
    # D4 铃铛立项：状态实际流转 → 定向回推会话属主（processing/resolved 可感知节点）
    if req.status != cur:
        notify_ticket_owner(db, t, req.status)
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
    return _item(t)
