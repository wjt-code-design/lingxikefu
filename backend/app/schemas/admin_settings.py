"""Admin Settings 响应模型（Phase 4）：只读配置视图，字段与 app.core.config.settings 对齐。"""
from __future__ import annotations

from pydantic import BaseModel


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


class AdminSettingsResp(BaseModel):
    env: str
    model: ModelSettings
    rag: RagSettings
    rate_limit: RateLimitSettings
    quota: QuotaSettings
