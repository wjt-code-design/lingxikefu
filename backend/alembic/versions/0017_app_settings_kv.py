"""app_settings KV 表（架构一期 6，2026-08-28）

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-28

- ``app_settings``：全局运行时设置 KV（key String(64) PK / tenant_id / value JSONB /
  updated_at）。首个消费者：配额上限 DB 化——``quota.daily_limit`` 行覆盖
  ``DAILY_QUOTA_LIMIT`` 常量，admin PUT /admin/settings/quota 写入，
  QuotaService.daily_limit() 60s TTL 缓存读取，大促免重启动态上调。
- 带 tenant_id 列与索引（红线⑨/ADR-2 全表惯例，tenant_id_column 工厂对称）；
  MVP 恒为 default 租户。与 per-user 用量表 ``quotas`` 无关。

写法照 0012（create_table + server_default now() + tenant 索引，downgrade 逆序对称）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_app_settings_tenant_id", "app_settings", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_app_settings_tenant_id", table_name="app_settings")
    op.drop_table("app_settings")
