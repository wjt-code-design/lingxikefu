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


class EvalGateResp(BaseModel):
    """KB 发布门禁 v1 观测面（架构三期 3，GET /admin/eval/gate）。

    passed 三态：True/False = 当前 kb_version 已有绑定评测（按 _pass_all 同阈值判）；
    None = 当前版本从未评测（含"有历史评测但绑定旧版本"），不误报。
    last_eval 结构：{run_id, created_at, metrics: [{metric, score, total, passed, status}]}。
    """

    kb_version: str | None = None
    last_eval: dict | None = None
    passed: bool | None = None
