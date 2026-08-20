"""Branch 3：messages 支持人工客服消息（role='agent' + agent 归属）

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-20

- ``message_role`` PG 枚举增加 ``'agent'``。注意：``ALTER TYPE ... ADD VALUE``
  不能在任何事务块内执行（即使语句本身被事务包裹），故包在 autocommit_block 中；
- messages 表新增 ``agent_id`` / ``agent_name`` 列（契约 ``Message.agent_id/agent_name``，
  人工客服归属；user/assistant 行为 NULL，无需回填）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PG 限制：ALTER TYPE ... ADD VALUE 不能在事务块内执行 → 临时切 autocommit
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE message_role ADD VALUE IF NOT EXISTS 'agent'")
    op.add_column("messages", sa.Column("agent_id", sa.String(64), nullable=True))
    op.add_column("messages", sa.Column("agent_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "agent_name")
    op.drop_column("messages", "agent_id")
    # PG 不支持删除枚举值；'agent' 值保留（无害）。彻底移除需重建枚举类型，不在本迁移处理。
