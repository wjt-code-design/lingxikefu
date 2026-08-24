"""管理后台响应模型（BU-09），与 contracts/api.ts Admin 段对齐。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Role = Literal["admin", "agent", "user"]


class UserItem(BaseModel):
    user_id: str
    account: str
    role: Role
    created_at: str


class UserListResp(BaseModel):
    items: list[UserItem]
    total: int


class RoleUpdateReq(BaseModel):
    role: Role


class HotGap(BaseModel):
    """F1：待补录问题（refuse 意图高频问句——QA 检索无依据被拒答，运营补 KB 信号）。
    handoff（转人工/情绪）是正常分流，不属于知识缺口，不计入。"""

    question: str
    count: int


class AdminStats(BaseModel):
    sessions: int
    messages: int
    documents: int
    feedback_up: int
    feedback_down: int
    avg_first_token_ms: float
    hot_gaps: list[HotGap] = []
    # T1.2 运营观测扩展（默认值保证旧调用方兼容）：
    # 真拒答轮数 = refuse_count - clarify_rounds（不变式：每澄清轮恰对应一个 refuse 用户消息）
    tool_dist: dict[str, int] = {}
    clarify_rounds: int = 0
    topic_dist: dict[str, int] = {}
    refuse_count: int = 0


class TrendPoint(BaseModel):
    """单日计数（P1：stats/trend）。"""

    date: str  # YYYY-MM-DD
    sessions: int
    messages: int
    tickets: int


class StatsTrendResp(BaseModel):
    days: list[TrendPoint]
