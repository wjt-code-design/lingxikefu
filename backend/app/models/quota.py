"""每日配额模型（BU-08 填充业务逻辑）。

关键约束：`(tenant_id, user_id, date)` 唯一 —— 每人每天一行，
配额扣减走行锁（SELECT ... FOR UPDATE），防超卖。
"""
from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class Quota(Base):
    __tablename__ = "quotas"
    __table_args__ = (
        sa.UniqueConstraint("tenant_id", "user_id", "date", name="uq_quotas_tenant_user_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    used: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0, server_default="0")
    limit: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=100, server_default="100")
