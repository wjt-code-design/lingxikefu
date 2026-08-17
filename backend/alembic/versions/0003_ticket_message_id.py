"""T1：tickets 表新增 message_id 列（溯源锚点，v2.1 修订 C）

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_tickets_message_id", "tickets", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_tickets_message_id", table_name="tickets")
    op.drop_column("tickets", "message_id")
