"""S2：tickets 表新增 version 列（乐观锁版本号）

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-18

- 与 app/models/ticket.py 一一对应；并发更新工单时以 ``UPDATE ... WHERE version=?`` 原子比较，
  防双客服并发操作「后者静默覆盖」导致审计与实况不一致。
- 已有行 version 走 server_default=0，无需回填。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("tickets", "version")
