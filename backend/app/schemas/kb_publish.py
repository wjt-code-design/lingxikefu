"""KB 发布批次请求/响应模型（门禁 v2 G2）。admin 编排面，前端暂无消费页面
（check_contracts IGNORE_EXTRA 白名单注明，消费时回填契约）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

BatchStatus = Literal["pending", "evaluating", "released", "failed", "rolled_back"]


class BatchEvalMetric(BaseModel):
    metric: str
    score: float
    total: int
    passed: int
    status: str


class BatchEvalSummary(BaseModel):
    run_id: str
    kb_version: str | None = None
    passed: bool | None = None
    metrics: list[BatchEvalMetric] = []


class BatchItem(BaseModel):
    batch_id: str
    kb_id: str
    kb_name: str | None = None
    status: BatchStatus
    doc_ids: list[str] = []
    doc_count: int = 0
    eval_result_id: str | None = None
    eval: BatchEvalSummary | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BatchListResp(BaseModel):
    items: list[BatchItem]


class BatchActionResp(BaseModel):
    """publish（202）/ rollback（200）动作响应。"""

    batch_id: str
    status: BatchStatus
    message: str = ""
