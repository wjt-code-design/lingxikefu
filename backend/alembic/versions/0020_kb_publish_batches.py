"""kb_publish_batches 发布批次表（门禁 v2 G2：batch 状态机 + 发布/回滚编排）

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-30

- ``kb_publish_batches``：一次"批量上传 → 抽样快检 → 发布/回滚"的编排单元。
  列：id (uuid PK) / tenant_id（红线⑨） / kb_id (FK CASCADE) / batch_id (String 64
  唯一索引) / status（PG enum ``kb_batch_status``：pending/evaluating/released/
  failed/rolled_back） / doc_ids (JSONB list[uuid]) / eval_result_id (FK eval_results
  SET NULL 可空，快检锚点行) / created_at / updated_at。
- 状态机：pending →（publish）→ evaluating →（快检 PASS）→ released /（FAIL）→ failed；
  released →（rollback）→ rolled_back；failed/rolled_back 可重发布。
- 枚举类型为新建（非 0009 的"加值"场景，CREATE TYPE 可在事务内执行，无需
  autocommit_block；后续若给 kb_batch_status 加值，照 0009 autocommit_block 先例）。

downgrade 逆序对称：删表 → 显式回收 enum 类型（drop_table 不级联删 type，0001 先例）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kb_publish_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column(
            "kb_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "evaluating",
                "released",
                "failed",
                "rolled_back",
                name="kb_batch_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("doc_ids", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "eval_result_id",
            sa.Uuid(),
            sa.ForeignKey("eval_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["eval_result_id"], ["eval_results.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_kb_publish_batches_tenant_id", "kb_publish_batches", ["tenant_id"])
    op.create_index("ix_kb_publish_batches_kb_id", "kb_publish_batches", ["kb_id"])
    op.create_index("ix_kb_publish_batches_batch_id", "kb_publish_batches", ["batch_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_kb_publish_batches_batch_id", table_name="kb_publish_batches")
    op.drop_index("ix_kb_publish_batches_kb_id", table_name="kb_publish_batches")
    op.drop_index("ix_kb_publish_batches_tenant_id", table_name="kb_publish_batches")
    op.drop_table("kb_publish_batches")
    # 显式回收 PostgreSQL enum 类型（drop_table 不级联删除 enum type，0001 先例）
    bind = op.get_bind()
    sa.Enum(name="kb_batch_status").drop(bind, checkfirst=True)
