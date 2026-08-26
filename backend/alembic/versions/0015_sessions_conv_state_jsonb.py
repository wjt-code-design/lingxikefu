"""sessions.conv_state 统一 JSONB（P4，2026-08-26）

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-26

与 messages.meta（已 JSONB）保持一致：PG 下 JSONB 支持索引/部分更新/查 json 路径，
sa.JSON 只能是 JSON 文本。仅改列类型（原数据经 USING cast 原位转换，无数据迁移）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 需 USING 透明转换既有 JSON 文本；SQLite 方言下 JSONB 表达式为 no-op（自动映射）
    op.alter_column(
        "sessions",
        "conv_state",
        type_=JSONB(),
        postgresql_using="conv_state::jsonb",
        existing_type=sa.JSON(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "sessions",
        "conv_state",
        type_=sa.JSON(),
        postgresql_using="conv_state::jsonb",
        existing_type=JSONB(),
        existing_nullable=True,
    )
