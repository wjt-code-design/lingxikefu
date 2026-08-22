"""工单状态自动化服务：自动状态流转。

四种自动判定机制：
1. 客服首次发言 → 工单 open → processing
2. 用户点满意反馈 → 工单 processing → resolved
3. 客服回复后超时无用户消息 → 工单 processing → resolved（可选）
4. 用户长时间未响应 → 工单 open/processing → closed

设计原则：
- 所有自动流转幂等（重复调用不报错）
- 流转用 UPDATE…WHERE 预期状态 的 CAS 原子写：SELECT 与 UPDATE 之间被并发
  流转（人工 PATCH / 另一自动化路径）时 rowcount=0，让位而不覆盖
- 迁移失败记录日志，不中断主流程；通过 settings 阈值配置时间窗口

边界契约（有意为之，改动需评估）：本服务函数内部自行 commit——调用方若有
未提交变更会被一并提交（如 sessions.post_agent_message 的客服消息随工单
流转同事务落库）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import aliased

from app.core.config import settings
from app.models.message import Message, MessageRole
from app.models.ticket import Ticket, TicketStatus

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
            Ticket.tenant_id == settings.TENANT_DEFAULT,
        )
    )
    if not t:
        return None

    result = db.execute(
        update(Ticket)
        .where(
            Ticket.id == t.id,
            Ticket.status == TicketStatus.open,  # CAS：间隙被并发流转则让位
            Ticket.tenant_id == settings.TENANT_DEFAULT,
        )
        .values(
            status=TicketStatus.processing,
            version=Ticket.version + 1,
            updated_at=datetime.now(UTC),
            **({"assignee_id": agent_id} if agent_id else {}),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if result.rowcount == 0:
        return None
    db.refresh(t)
    logger.info(
        "ticket_auto: open→processing (agent first reply) ticket=%s", t.id
    )
    return t


def auto_resolve_on_positive_feedback(
    db: OrmSession, session_id: uuid.UUID
) -> Ticket | None:
    """用户点满意反馈时：将 processing 状态工单流转为 resolved。

    幂等：已不是 processing 的工单直接跳过（CAS 未命中返回 None）。

    语义（有意设计，2026-08-22 审查定案）：会话内任意 up 反馈即触发——含对 AI
    回复的点赞（UI 的点赞入口仅在 AI 消息上，收紧到"客服消息"会让触发器不可达）。
    用户在客服处理中点赞旧 AI 回复也会关单，代价是偶尔提前 resolved（不阻塞
    客服后续跟进）；收益是满意的用户无需任何额外操作。若产品要求更严，需先给
    消息加可标注的"本次服务结论"信号，再收紧此处。
    """
    t = db.scalar(
        select(Ticket).where(
            Ticket.session_id == session_id,
            Ticket.status == TicketStatus.processing,
            Ticket.tenant_id == settings.TENANT_DEFAULT,
        )
    )
    if not t:
        return None

    result = db.execute(
        update(Ticket)
        .where(
            Ticket.id == t.id,
            Ticket.status == TicketStatus.processing,  # CAS：间隙被并发流转则让位
            Ticket.tenant_id == settings.TENANT_DEFAULT,
        )
        .values(
            status=TicketStatus.resolved,
            version=Ticket.version + 1,
            updated_at=datetime.now(UTC),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if result.rowcount == 0:
        return None
    db.refresh(t)
    logger.info(
        "ticket_auto: processing→resolved (positive feedback) ticket=%s", t.id
    )
    return t


def auto_resolve_after_timeout(
    db: OrmSession, timeout_minutes: int | None = None
) -> list[Ticket]:
    """客服回复后超时无用户新消息：processing 工单 → resolved。

    候选（单查询，免逐单 N+1）：存在一条"同会话其后无任何消息"的 agent 消息
    （即最后一条消息来自客服）且该消息早于 cutoff 的 processing 工单。
    仅处理配置开启（AUTO_TICKET_RESOLVE_TIMEOUT_MIN > 0）的环境。
    """
    # 显式 None 才回退配置：0 是"关闭"信号（`or` 会把 0 当假值吞掉，导致关不掉）
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
            Ticket.tenant_id == settings.TENANT_DEFAULT,
            Message.role == MessageRole.agent,
            Message.created_at < cutoff,
            # 该 agent 消息之后同会话再无任何消息 ⇒ 它就是最后一条
            # （created_at 完全同刻的两条消息属未定义边界，业务上不会出现）
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
            result = db.execute(
                update(Ticket)
                .where(
                    Ticket.id == t.id,
                    Ticket.status == TicketStatus.processing,  # CAS：并发流转则让位
                    Ticket.tenant_id == settings.TENANT_DEFAULT,
                )
                .values(
                    status=TicketStatus.resolved,
                    version=Ticket.version + 1,
                    updated_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            if result.rowcount == 0:
                continue
            db.refresh(t)
            resolved.append(t)
            logger.info(
                "ticket_auto: processing→resolved (timeout=%dm) ticket=%s",
                minutes,
                t.id,
            )
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
    # 显式 None 才回退配置：0 是"关闭"信号（`or` 会把 0 当假值吞掉，导致关不掉）
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
            Ticket.tenant_id == settings.TENANT_DEFAULT,
        )
    ).all()

    for t in tickets:
        try:
            prev_status = t.status.value  # 变更前状态（变更后取值恒为 closed，日志会失真）
            result = db.execute(
                update(Ticket)
                .where(
                    Ticket.id == t.id,
                    Ticket.status.in_([TicketStatus.open, TicketStatus.processing]),
                    Ticket.updated_at < cutoff,  # CAS：复查候选条件仍成立
                    Ticket.tenant_id == settings.TENANT_DEFAULT,
                )
                .values(
                    status=TicketStatus.closed,
                    version=Ticket.version + 1,
                    updated_at=datetime.now(UTC),
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            if result.rowcount == 0:
                continue
            db.refresh(t)
            closed.append(t)
            logger.info(
                "ticket_auto: %s→closed (idle=%dd) ticket=%s",
                prev_status,
                days,
                t.id,
            )
        except Exception:
            logger.exception("ticket_auto: close stale failed for ticket %s", t.id)
            db.rollback()

    return closed
