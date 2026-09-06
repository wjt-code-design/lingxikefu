"""Admin Settings 路由：/api/v1/admin/settings 配置只读视图。

- GET /settings：配置只读视图，各分组从 settings 单一真源读取。
- （2026-09-06 额度系统移除：PUT /settings/quota 每日配额写通道随配额机制下线；
  原 quota 分组读 app_settings KV 生效值的逻辑一并移除。）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.core.config import settings
from app.schemas.admin_settings import (
    AdminSettingsResp,
    ModelSettings,
    RagSettings,
    RateLimitSettings,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/settings", response_model=AdminSettingsResp)
def get_admin_settings(_: dict = Depends(require_admin)) -> AdminSettingsResp:
    """返回当前运行配置（全部从 settings 单一真源读取）。"""
    return AdminSettingsResp(
        env=settings.ENV,
        model=ModelSettings(
            provider=settings.CHAT_PROVIDER,
            # 2026-08-27 全面取消其他平台：对话模型仅 LongCat，无备用模型（fallback=None）
            model=settings.LONGCAT_CHAT_MODEL,
            fallback=None,
            embedding_provider=settings.EMBEDDING_PROVIDER,
            embedding_model=settings.EMBEDDING_MODEL,
        ),
        rag=RagSettings(
            top_k=settings.RETRIEVAL_TOP_K,
            min_score=settings.MIN_SCORE,
            hybrid=settings.RAG_ENABLE_HYBRID,
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            answer_cache_enabled=settings.ANSWER_CACHE_ENABLED,
            answer_cache_threshold=settings.ANSWER_CACHE_THRESHOLD,
            max_upload_mb=settings.MAX_UPLOAD_MB,
        ),
        rate_limit=RateLimitSettings(enabled=settings.RATE_LIMIT_ENABLED),
    )
