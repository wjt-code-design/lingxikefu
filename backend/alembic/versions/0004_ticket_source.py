"""P0-4：tickets 表新增 source 列（ai/manual，用户主动转人工来源标记）

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default="ai",  # 既有工单默认 ai（LLM 自动建单）
        ),
    )


def downgrade() -> None:
    op.drop_column("tickets", "source")
