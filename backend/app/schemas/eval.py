"""评测中心 schema：历史查询 + 触发评测。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class EvalResultItem(BaseModel):
    run_id: str
    metric: str
    score: float
    total: int
    passed: int
    status: str
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EvalHistoryResp(BaseModel):
    items: list[EvalResultItem]


class EvalTriggerReq(BaseModel):
    """触发评测请求体。"""
    limit: int = 0  # 0=全部
    kb_name: str | None = None


class EvalTriggerResp(BaseModel):
    run_id: str
    status: str
    message: str
