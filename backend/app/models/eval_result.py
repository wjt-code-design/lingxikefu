"""评测结果存储：每次评测运行的指标快照。

设计：
- 每次评测运行一条记录（run_id 聚合该次所有指标）
- 指标类型：faithfulness / recall / citation / refuse 等
- 前端据此画历史趋势图 + 退化告警
"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class EvalStatus:
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()

    # 运行标识（一次评测运行 = 同 run_id 的多条指标）
    run_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    # 指标类型：faithfulness / recall / citation / refuse / refuse_qa / handoff / chitchat
    metric: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    # 分数（0~1）
    score: Mapped[float] = mapped_column(sa.Float(), nullable=False)
    # 样本数
    total: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    # 通过数
    passed: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    # 运行状态
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=EvalStatus.DONE
    )
    # 失败明细（JSON 数组，可选）
    failures: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    # 触发来源：manual / ci / scheduled
    source: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="manual")
    # KB 版本指纹（三期 3 发布门禁 v1：本行指标"评的是哪个版本"）。
    # 公式单一真源 kb_lookup.kb_version_str（就绪文档数:最新文档 created_at，与 chat 缓存
    # 失效锚点同式）；触发链在每阶段完成时写入。可空：存量行 / CLI 直跑 / 版本解析失败
    # （fail-open 不阻塞评测）不绑定，gate 端点按"当前版本未评测"处理（不误报）。
    kb_version: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
