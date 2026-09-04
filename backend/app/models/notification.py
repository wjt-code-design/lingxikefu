"""通知模型（通知中心 SSE）：接收方角色 + 事件类型 + 已读状态。

- recipient_role：agent / admin（MVP 按角色路由，不做按人指派）；
- is_read 建索引支撑「未读数」统计（角标轮询 + 列表未读在前排序）；
- 按红线⑨ / ADR-2 全表含 tenant_id（id 之后），风格对齐 audit_logs。
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    # 红线⑨ / ADR-2：全表 tenant_id，id 之后显式声明，自动建 ix_notifications_tenant_id 索引
    tenant_id: Mapped[str] = tenant_id_column()
    recipient_role: Mapped[str] = mapped_column(sa.String(16), nullable=False, index=True)
    # 按人投递（2026-09-04 D4 铃铛立项）：NULL = 角色广播（agent/admin 旧语义）；
    # 非空 = 定向到该用户（查询/SSE 推送均按此过滤）。user 角色通知必须定向。
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    content: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="")
    resource_type: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    is_read: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=False,
        server_default=sa.text("false"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
