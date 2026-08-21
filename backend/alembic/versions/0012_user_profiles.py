"""用户画像表（长期记忆，2026-08-22，Phase A）

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-22

- ``user_profiles``：1:1 关联 users，JSONB 存聚合画像（常问主题/实体/满意度/偏好）；
- ``version`` 乐观锁：并发增量合并防丢更新；
- 唯一约束 (tenant_id, user_id)；用户删除级联清画像。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_user_profiles_tenant_user"),
    )
    op.create_index("ix_user_profiles_user_id", "user_profiles", ["user_id"])
    op.create_index("ix_user_profiles_tenant_id", "user_profiles", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_user_profiles_tenant_id", table_name="user_profiles")
    op.drop_index("ix_user_profiles_user_id", table_name="user_profiles")
    op.drop_table("user_profiles")
