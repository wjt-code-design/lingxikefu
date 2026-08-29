"""工单模型（Phase2 预留，MVP 不实现，表结构先建）。

AI 判定转人工 → 建单 → 状态机（open→processing→resolved/closed）→ 分配给客服。
external_ref 用于 Phase2 对接飞书 / Jira / TAPD 的 TicketAdapter 抽象。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class TicketStatus(StrEnum):
    open = "open"
    processing = "processing"
    resolved = "resolved"
    closed = "closed"


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # T1 溯源锚点（v2.1 修订 C）：建单时记录触发消息，问题追踪溯源的起点。
    # 消息删除（随会话级联）时 SET NULL，工单保留。
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[TicketStatus] = mapped_column(
        sa.Enum(TicketStatus, name="ticket_status"),
        nullable=False,
        default=TicketStatus.open,
        server_default=TicketStatus.open.value,
    )
    # 建单来源：ai（LLM 意图自动）/ manual（用户主动转人工按钮）
    source: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="ai", server_default="ai"
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_ref: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    # 架构一期 4：AI handoff 建单时持久化移交摘要（build_handoff_summary 产物的 JSON 文本，
    # ensure_ascii=False），坐席首屏直接看到主题/槽位/澄清状态，不再从零重问。
    # 仅新建时落库；manual 建单与历史行为 NULL。
    summary: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # S2 乐观锁版本号：每次状态流转/分配 version+1；并发更新时以 version 条件做原子比较，防后者静默覆盖
    version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    # 架构一期 4：逐状态流转时间戳（状态机 CAS update 与 PATCH 流转按目标状态补记；
    # closed 无独立列，updated_at 已覆盖）。存量行 NULL（创建时点不可考，不回填）。
    processing_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
