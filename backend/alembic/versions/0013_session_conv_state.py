"""会话状态机列（批次B，2026-08-24）

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-24

- ``sessions.conv_state``：JSON 可空——{stage, topic, slots, clarify_count}；
- 存量会话 NULL = 无状态（代码按 new_state 处理），无需数据回填。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("conv_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "conv_state")
