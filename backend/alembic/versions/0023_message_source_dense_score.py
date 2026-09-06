"""溯源相似度口径修复（A，2026-09-06）

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-06

- message_sources.dense_score：可空 Float。dense 原始余弦相似度（绝对语义），
  与 score（hybrid 下=RRF 融合分，仅排名语义）并列存储。
- 溯源面板「相似度」标签消费 dense_score；存量行 NULL → 前端回退 score（不比现状差）。

写法照 0021（可空加列），upgrade/downgrade 对称。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message_sources",
        sa.Column("dense_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("message_sources", "dense_score")
