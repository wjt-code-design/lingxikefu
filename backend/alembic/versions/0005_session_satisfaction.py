"""P2-2：sessions 表新增 satisfaction 列（会话级满意度，nullable）

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("satisfaction", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "satisfaction")
