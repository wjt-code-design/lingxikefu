"""用户画像模型（长期记忆，2026-08-22，Phase A）。

跨会话用户画像（long-term memory）：
- 1:1 关联 users，JSONB 存聚合画像摘要（常问主题/实体/满意度/偏好）；
- version 乐观锁：并发增量合并防丢更新（multi-worker 场景）；
- 个人上下文**绝不进入** answer_cache（约束由 Phase B 的采集服务实现时落实）；
- 删除用户（T4）级联清除画像（ondelete CASCADE）。

画像 JSONB 结构（schema_version 向前兼容）由 Phase B 的 user_profile_service 定义。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_user_profiles_tenant_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    # 乐观锁：每次增量合并 version+1，更新条件带 version，防多 worker 并发丢更新
    version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
