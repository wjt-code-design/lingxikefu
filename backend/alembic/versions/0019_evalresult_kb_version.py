"""eval_results 绑定 KB 版本指纹（架构三期 3：KB 发布门禁 v1 观测面）

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-28

- eval_results.kb_version：可空 String(255)。评测触发链（POST /admin/eval/run →
  _do_eval）在每阶段完成落表时把当时 KB 版本指纹（kb_lookup.kb_version_str 单一真源，
  与 chat 缓存失效锚点同式）写入，gate 端点据此比对"当前版本是否评测通过"。
- 历史行 / CLI 直跑路径 / 版本解析失败（fail-open）为 NULL，不回填——gate 端点按
  "当前版本从未评测"（passed=None）处理，不误报。

⚠️ 缺口修补（现场查证 2026-08-28）：eval_results 自 P1 评测中心上线起**没有建表迁移**
（0001-0018 均不涉及，本地/演示环境靠 Base.metadata.create_all 兜底），fresh PG 部署链
（CI / docker compose migrate = alembic upgrade head）在本迁移 ALTER 时必炸
（relation "eval_results" does not exist）。故本迁移先条件建表（形状与
app.models.eval_result 对齐，含红线⑨ tenant_id 索引与 run_id 索引），再统一加列；
表已存在的环境（本地/演示）直接走加列，等价 0018/0009 的可空加列惯例。

downgrade 只删 kb_version 列、不删表：0019 建出的表在 downgrade 后残留为空表（无害，
再 upgrade 因存在性检查跳过建表、补列成功）；存量表环境则精确还原原形状。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def _eval_results_table_absent(bind) -> bool:
    """PG 专用存在性检查（to_regclass 对缺失表返回 NULL）。"""
    return bind.execute(sa.text("SELECT to_regclass('eval_results') IS NULL")).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    created_here = _eval_results_table_absent(bind)
    if created_here:
        # 形状与 app/models/eval_result.py 逐列对齐（kb_version 由下方统一加列，不在此处）
        op.create_table(
            "eval_results",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("metric", sa.String(length=32), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("total", sa.Integer(), nullable=False),
            sa.Column("passed", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("failures", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=16), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_eval_results_tenant_id", "eval_results", ["tenant_id"])
        op.create_index("ix_eval_results_run_id", "eval_results", ["run_id"])
    op.add_column("eval_results", sa.Column("kb_version", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("eval_results", "kb_version")
    # 0019 条件建出的表不在此删除：re-upgrade 幂等（存在性检查跳过建表），空表残留无害。
