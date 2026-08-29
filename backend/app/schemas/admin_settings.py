"""Admin Settings 响应模型（Phase 4）：只读配置视图，字段与 app.core.config.settings 对齐。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ModelSettings(BaseModel):
    provider: str
    model: str
    fallback: str | None = None
    embedding_provider: str
    embedding_model: str


class RagSettings(BaseModel):
    top_k: int
    min_score: float
    hybrid: bool
    chunk_size: int
    chunk_overlap: int
    answer_cache_enabled: bool
    answer_cache_threshold: float
    max_upload_mb: int


class RateLimitSettings(BaseModel):
    enabled: bool


class QuotaSettings(BaseModel):
    daily_limit: int


class QuotaSettingsUpdate(BaseModel):
    """PUT /admin/settings/quota 请求体：每日配额上限写通道（架构一期 6，大促动态上调）。

    >0 强约束（≤0 会让 try_consume 全量拒绝）；非整数 / 带小数由 int 校验拒绝 → 422。
    """

    daily_quota_limit: int = Field(gt=0, description="每日配额上限（正整数）")


class AdminSettingsResp(BaseModel):
    env: str
    model: ModelSettings
    rag: RagSettings
    rate_limit: RateLimitSettings
    quota: QuotaSettings
