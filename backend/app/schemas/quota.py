"""Quota 响应模型（与 contracts/api.ts QuotaResp 对应）。"""
from __future__ import annotations

from pydantic import BaseModel


class QuotaResp(BaseModel):
    date: str
    used: int
    limit: int
    left: int
