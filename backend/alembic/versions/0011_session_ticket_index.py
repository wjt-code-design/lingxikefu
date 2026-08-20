"""第三批#3/优化项1：高频过滤/排序列补索引

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-20

- ``sessions.updated_at``：会话列表按 updated_at desc 排序（工作台/审计页高频路径，此前全表扫描）；
- ``tickets.status``：工单列表按状态过滤（open/processing 分状态拉取，后台待办常驻查询）。

均为提升型索引（不影响行为）；配合 BUG-03 的 session touch，更新触发索引维护属正常成本。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_sessions_updated_at", "sessions", ["updated_at"])
    op.create_index("ix_tickets_status", "tickets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tickets_status", table_name="tickets")
    op.drop_index("ix_sessions_updated_at", table_name="sessions")