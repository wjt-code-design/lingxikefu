"""P2-修复#2：用户意见反馈表 suggestions

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-20

- 意见反馈页（FeedbackPage）真实落库：此前前端假提交（setTimeout + toast），
  用户反馈全部丢弃；
- ``type`` 用 PG 枚举 ``suggestion_type``（bug/suggestion/other）；
- ``user_id`` 外键 users（CASCADE），提交人可追溯。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    suggestion_type = sa.Enum("bug", "suggestion", "other", name="suggestion_type")
    op.create_table(
        "suggestions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", suggestion_type, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contact", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_suggestions_user_id", "suggestions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_suggestions_user_id", table_name="suggestions")
    op.drop_table("suggestions")
    op.execute("DROP TYPE IF EXISTS suggestion_type")
