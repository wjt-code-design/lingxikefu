"""审计日志模型（Phase 4）：管理端 / 客服关键操作的可追溯记录。

- actor：操作人（id / email / role）；action + resource 描述操作类型与对象；
- resource_id / detail / ip 补充定位信息；
- created_at 采用 base.py / ticket.py 一致的 ``server_default=func.now()`` 风格；
- 按红线⑨ / ADR-2 全表含 tenant_id（id 之后），审计记录随租户隔离；业务上不强制按 tenant 过滤。
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    # 红线⑨ / ADR-2：全表 tenant_id，id 之后显式声明，自动建 ix_audit_logs_tenant_id 索引
    tenant_id: Mapped[str] = tenant_id_column()
    actor_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    actor_email: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    action: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    resource: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    ip: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
