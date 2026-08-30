"""KB 发布批次模型（门禁 v2 G2：batch 状态机 + staged 发布/回滚编排）。

- 批次 = 一次"批量上传 → 抽样快检 → 发布/回滚"的编排单元；batch_id 由调用方提供
  （String 64，唯一索引），首个带 batch_id 的上传隐式建行，后续上传追加 doc_ids。
- 状态机：pending →（publish）→ evaluating →（快检 PASS）→ released /（FAIL）→ failed；
  released →（rollback）→ rolled_back；failed / rolled_back 可重新 publish（重跑快检）。
- doc_ids 为 JSONB list[uuid str]（批次文档清单）；eval_result_id 指向本次快检的
  锚点行（同 run_id 聚合全部指标行，SET NULL：评测行删除不阻塞批次展示）。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column

#: SQLite 无 JSONB 类（同一约定见 Session.conv_state / AppSetting.value）
_JSONB = JSONB().with_variant(sa.JSON(), "sqlite")


class KBBatchStatus(StrEnum):
    """发布批次状态（PG enum kb_batch_status，迁移 0020 同名创建）。"""

    pending = "pending"
    evaluating = "evaluating"
    released = "released"
    failed = "failed"
    rolled_back = "rolled_back"


class KBPublishBatch(Base):
    __tablename__ = "kb_publish_batches"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    kb_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True, index=True)
    status: Mapped[KBBatchStatus] = mapped_column(
        sa.Enum(KBBatchStatus, name="kb_batch_status"),
        nullable=False,
        default=KBBatchStatus.pending,
        server_default=KBBatchStatus.pending.value,
    )
    # 批次文档清单（list[uuid str]；发布时按此校验/翻转）
    doc_ids: Mapped[list] = mapped_column(_JSONB, nullable=False, default=list)
    # 本次快检锚点行（同 run_id 聚合全部指标行）；重发布清空，评测完成回填
    eval_result_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("eval_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
