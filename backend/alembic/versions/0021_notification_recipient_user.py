"""通知按人投递（D4 铃铛立项，2026-09-04）

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-04

- notifications.recipient_user_id：可空 UUID + 索引。NULL = 角色广播
  （agent/admin 旧语义完全不变）；非空 = 定向投递（仅该用户列表/未读可见，
  SSE 按订阅者 user_id 过滤）。user 角色通知强制定向（API 层保证）。

写法照 0018（可空加列）+ 0011（create_index），upgrade/downgrade 逆序对称。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_notifications_recipient_user_id", "notifications", ["recipient_user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_recipient_user_id", table_name="notifications")
    op.drop_column("notifications", "recipient_user_id")
