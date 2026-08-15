"""BU-04：documents 表新增 raw_text 列（解析后文本，worker 切片/向量化用）

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("raw_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "raw_text")
