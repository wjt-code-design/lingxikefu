"""高频排序/过滤列补索引（B2-2，2026-09-06 深度审查）

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-06

按实际查询形态补复合索引（执行前 pg_indexes 实查现状，只建确认缺失者）：
- ``tickets(tenant_id, updated_at)``：工单列表 ``WHERE tenant_id[, status]
  ORDER BY updated_at DESC``（tickets.py:185）——tenant 定位 + updated_at 免排序；
- ``audit_logs(tenant_id, created_at)``：审计列表 ``WHERE tenant_id ORDER BY
  created_at DESC``（audit_logs.py:65），表只增不删，随时间线性劣化，最先需要；
- ``feedback(tenant_id, created_at)``：差评列表 ``WHERE tenant_id AND rating
  ORDER BY created_at DESC``（admin.py:435）——tenant 前缀 + 排序，rating 残余过滤；
- ``messages(session_id, created_at)``：chat 热路径历史 ``WHERE session_id
  ORDER BY created_at DESC LIMIT 6``（chat.py _fetch_history）——前缀 session_id
  定位 + created_at 已序免 sort。现有单列 session_id/created_at 各自无法覆盖此复合。

索引仅提速查询，不改数据语义，可安全 up/down 对称。复合索引只在迁移声明
（照 0014 先例，models 用列级 index=True 覆盖单列，复合不镜像 metadata）。
"""
from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_tickets_tenant_updated_at", "tickets", ["tenant_id", "updated_at"])
    op.create_index("ix_audit_logs_tenant_created_at", "audit_logs", ["tenant_id", "created_at"])
    op.create_index("ix_feedback_tenant_created_at", "feedback", ["tenant_id", "created_at"])
    op.create_index("ix_messages_session_created_at", "messages", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_messages_session_created_at", table_name="messages")
    op.drop_index("ix_feedback_tenant_created_at", table_name="feedback")
    op.drop_index("ix_audit_logs_tenant_created_at", table_name="audit_logs")
    op.drop_index("ix_tickets_tenant_updated_at", table_name="tickets")
