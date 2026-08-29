"""tickets 移交摘要 + 流转时间戳（架构一期 4，2026-08-28）

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-28

- tickets.summary：AI handoff 建单时持久化移交摘要（build_handoff_summary 产物的 JSON
  文本，ensure_ascii=False），坐席首屏直接看到主题/槽位/澄清状态，不再从零重问；
  可空（manual 建单与存量行 NULL）。
- tickets.processing_at / resolved_at：逐状态流转时间戳（状态机 CAS update 与 PATCH
  流转按目标状态补记；closed 无独立列，updated_at 已覆盖）。存量行 NULL，不回填。

写法照 0009（无索引/无 FK 的可空加列，upgrade 顺序与 downgrade 逆序对称）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("tickets", sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tickets", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("tickets", "resolved_at")
    op.drop_column("tickets", "processing_at")
    op.drop_column("tickets", "summary")
