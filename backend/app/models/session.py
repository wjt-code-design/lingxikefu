"""会话模型（BU-03 填充业务逻辑）。"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column

#: P4：conv_state 统一 JSONB（与 messages.meta 一致，PG 可 json 路径查询/部分更新）。
#: SQLite 无 JSONB 类（模型若无变体则 SQLite DDL 编译崩——同一约定见 Message.meta 测试
#: 建表前替换类型）；用 with_variant 让两种方言各取所需，测试零改动。
_JSONB = JSONB().with_variant(sa.JSON(), "sqlite")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
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
    satisfaction: Mapped[str | None] = mapped_column(
        sa.String(16),
        nullable=True,
        default=None,
        comment="会话级满意度（P2-2）：satisfied / neutral / unsatisfied",
    )
    # 批次B（2026-08-24）：会话状态机——阶段+槽位跨轮持久化（conversation_state.py 管结构）
    conv_state: Mapped[dict | None] = mapped_column(
        _JSONB,  # P4：与 messages.meta 统一 JSONB（PG=JSONB，SQLite=JSON 变体）
        nullable=True,
        default=None,
        comment="会话状态机：{stage, topic, slots, clarify_count}（app/services/conversation_state.py）",
    )
