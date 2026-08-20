"""消息与知识来源模型。

- messages：会话内的一条消息。
- message_sources：assistant 回答引用的知识块，**知识来源唯一真源**（规划书红线⑨，
  废弃「模型手写 source」双轨）。BU-06/BU-07 落库。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"
    agent = "agent"  # Branch 3：人工客服消息（契约 P2 角色）


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        sa.Enum(MessageRole, name="message_role"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    intent: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    # Branch 3：人工客服消息归属（契约 Message.agent_id / agent_name；user/assistant 为 NULL）
    agent_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    agent_name: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sa.text("'{}'::jsonb"),
    )


class MessageSource(Base):
    __tablename__ = "message_sources"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    message_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False, index=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False, index=True)
    doc_title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    snippet: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    score: Mapped[float] = mapped_column(sa.Float(), nullable=False)
