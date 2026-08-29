"""tickets AI 预起草（架构二期 1，L2 预起草，2026-08-28）

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-28

- tickets.draft_suggestion：low risk handoff 建单后后台 AI 预草拟的回复
  （agent_assist.draft_reply 产物，复用 suggest 端点核心），坐席打开工单即见草稿。
  可空（drafting fail-open 留空；manual 建单与存量行 NULL）。
- tickets.draft_kind：草稿种类，"ai" = AI 预起草；预留 M7 草稿确认流的人工编辑态。
  未起草 NULL。

写法照 0016/0009（无索引/无 FK 的可空加列，upgrade 顺序与 downgrade 逆序对称）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("draft_suggestion", sa.Text(), nullable=True))
    op.add_column("tickets", sa.Column("draft_kind", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "draft_kind")
    op.drop_column("tickets", "draft_suggestion")
