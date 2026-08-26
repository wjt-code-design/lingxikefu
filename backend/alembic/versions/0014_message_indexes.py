"""messages 时间/租户/意图索引（P2-⑧，2026-08-26）

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-26

admin trend / 列表高频查询路径补索引，避免全表扫描：
- ``messages(created_at)``：趋势按日聚合、最近消息排序；
- ``messages(tenant_id, created_at)``：租户内趋势/排序复合索引；
- ``messages(intent)``：统计/筛选按意图分组。

索引仅提速查询，不改数据语义，可安全 up/down 对称。
"""
from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index("ix_messages_tenant_created_at", "messages", ["tenant_id", "created_at"])
    op.create_index("ix_messages_intent", "messages", ["intent"])


def downgrade() -> None:
    op.drop_index("ix_messages_intent", table_name="messages")
    op.drop_index("ix_messages_tenant_created_at", table_name="messages")
    op.drop_index("ix_messages_created_at", table_name="messages")
