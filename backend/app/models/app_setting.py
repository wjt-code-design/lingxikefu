"""app_settings KV 模型（架构一期 6）：运行时可写配置存储。

本表是**全局设置 KV**（配额计数走 Redis QuotaService，无 per-user 用量表——
alembic 0001：配额改用 Redis 计数，无 quotas 表）：key 覆盖 app.core.config 中的
运行时配置项（如 ``quota.daily_limit`` → ``DAILY_QUOTA_LIMIT``），值统一 JSON 列。
首个消费者：QuotaService.daily_limit() 的动态上限（大促免重启上调），写入通道为
admin PUT /admin/settings/quota。

按红线⑨/ADR-2 惯例带 tenant_id 列（tenant_id_column() 工厂，第一个非主键列 + 索引）：
MVP 单租户恒为 settings.TENANT_DEFAULT，读取侧按租户过滤，Phase3 行级过滤时无需改表。
"""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column

#: PG=JSONB（可 json 路径查询），SQLite=JSON 变体（同一约定见 Session.conv_state）
_JSONB = JSONB().with_variant(sa.JSON(), "sqlite")


class AppSetting(Base):
    __tablename__ = "app_settings"

    #: 配置键（如 quota.daily_limit），String(64) 主键
    key: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    tenant_id: Mapped[str] = tenant_id_column()
    #: JSON 值（配额上限存 JSON 标量 int；bool 是 int 子类，读取侧需排除）
    value: Mapped[dict | int] = mapped_column(_JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
