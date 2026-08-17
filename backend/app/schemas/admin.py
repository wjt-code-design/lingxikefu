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
    """F1：待补录问题（handoff/refuse 高频问句，运营补 KB 信号）。"""

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
