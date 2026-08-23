"""工单状态自动化服务：自动状态流转。

四种自动判定机制（调用方不变，内部统一走状态机）：
1. 客服首次发言 → 工单 open → processing（event: agent_first_reply）
2. 用户点满意反馈 → 工单 processing → resolved（event: positive_feedback）
3. 客服回复后超时无用户消息 → 工单 processing → resolved（event: agent_reply_timeout）
4. 用户长时间未响应 → 工单 open/processing → closed（event: idle_timeout）

边界契约（有意为之，改动需评估）：本服务函数内部自行 commit——调用方若有
未提交变更会被一并提交（如 sessions.post_agent_message 的客服消息随工单
流转同事务落库）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.core.tenant import get_current_tenant
from app.models.message import Message, MessageRole
from app.models.ticket import Ticket, TicketStatus
from app.services.ticket_state_machine import transition

logger = logging.getLogger(__name__)


def auto_start_processing(
    db: OrmSession, session_id: uuid.UUID, agent_id: uuid.UUID | None = None
) -> Ticket | None:
    """客服首次发言时：将该 session 下 open 状态工单流转为 processing。

    幂等：已不是 open 的工单直接跳过（CAS 未命中返回 None）。
    """
    t = db.scalar(
        select(Ticket).where(
            Ticket.session_id == session_id,
            Ticket.status == TicketStatus.open,
            Ticket.tenant_id == get_current_tenant(),
        )
    )
    if not t:
        return None

    return transition(db, t.id, "agent_first_reply", assignee_id=agent_id)


def auto_resolve_on_positive_feedback(
    db: OrmSession, session_id: uuid.UUID
) -> Ticket | None:
    """用户点满意反馈时：将 processing 状态工单流转为 resolved。

    幂等：已不是 processing 的工单直接跳过（CAS 未命中返回 None）。
    """
    t = db.scalar(
        select(Ticket).where(
            Ticket.session_id == session_id,
            Ticket.status == TicketStatus.processing,
            Ticket.tenant_id == get_current_tenant(),
        )
    )
    if not t:
        return None

    return transition(db, t.id, "positive_feedback")


def auto_resolve_after_timeout(
    db: OrmSession, timeout_minutes: int | None = None
) -> list[Ticket]:
    """客服回复后超时无用户新消息：processing 工单 → resolved。

    候选（单查询，免逐单 N+1）：存在一条"同会话其后无任何消息"的 agent 消息
    （即最后一条消息来自客服）且该消息早于 cutoff 的 processing 工单。
    仅处理配置开启（AUTO_TICKET_RESOLVE_TIMEOUT_MIN > 0）的环境。
    """
    minutes = (
        timeout_minutes
        if timeout_minutes is not None
        else settings.AUTO_TICKET_RESOLVE_TIMEOUT_MIN
    )
    if minutes <= 0:
        return []

    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    later = aliased(Message)
    tickets = db.scalars(
        select(Ticket)
        .join(Message, Message.session_id == Ticket.session_id)
        .where(
            Ticket.status == TicketStatus.processing,
            Ticket.tenant_id == get_current_tenant(),
            Message.role == MessageRole.agent,
            Message.created_at < cutoff,
            ~exists(
                select(later.id).where(
                    later.session_id == Ticket.session_id,
                    later.created_at > Message.created_at,
                )
            ),
        )
    ).all()

    resolved: list[Ticket] = []
    for t in tickets:
        try:
            result = transition(db, t.id, "agent_reply_timeout")
            if result is not None:
                resolved.append(result)
        except Exception:
            logger.exception("ticket_auto: resolve timeout failed for ticket %s", t.id)
            db.rollback()

    return resolved


def auto_close_stale(
    db: OrmSession, idle_days: int | None = None
) -> list[Ticket]:
    """用户长时间未响应：open/processing 工单 → closed。

    仅处理配置开启（AUTO_TICKET_CLOSE_IDLE_DAYS > 0）的环境。
    """
    days = (
        idle_days if idle_days is not None else settings.AUTO_TICKET_CLOSE_IDLE_DAYS
    )
    if days <= 0:
        return []

    cutoff = datetime.now(UTC) - timedelta(days=days)
    closed: list[Ticket] = []

    tickets = db.scalars(
        select(Ticket).where(
            Ticket.status.in_([TicketStatus.open, TicketStatus.processing]),
            Ticket.updated_at < cutoff,
            Ticket.tenant_id == get_current_tenant(),
        )
    ).all()

    for t in tickets:
        try:
            result = transition(db, t.id, "idle_timeout")
            if result is not None:
                closed.append(result)
        except Exception:
            logger.exception("ticket_auto: close stale failed for ticket %s", t.id)
            db.rollback()

    return closed
