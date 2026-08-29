"""Admin Settings 路由：/api/v1/admin/settings 配置视图。

- GET /settings：配置只读视图。quota 组读**生效值**（app_settings KV 覆盖优先，
  QuotaService.daily_limit()），其余分组仍从 settings 单一真源读取。
- PUT /settings/quota：每日配额上限写通道（架构一期 6）——写 app_settings KV 并
  清 60s 生效缓存，大促免重启动态上调；仅 admin（require_admin）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.core.database import get_db
from app.schemas.admin_settings import (
    AdminSettingsResp,
    ModelSettings,
    QuotaSettings,
    QuotaSettingsUpdate,
    RagSettings,
    RateLimitSettings,
)
from app.services.quota import get_quota_service

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/settings", response_model=AdminSettingsResp)
def get_admin_settings(_: dict = Depends(require_admin)) -> AdminSettingsResp:
    """返回当前运行配置（quota 组为 DB 生效值，其余分组从 settings 单一真源读取）。"""
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
        quota=QuotaSettings(daily_limit=get_quota_service().daily_limit()),
    )


@router.put("/settings/quota", response_model=QuotaSettings)
def put_quota_settings(
    body: QuotaSettingsUpdate,
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> QuotaSettings:
    """更新每日配额上限：写 app_settings KV + 清 60s 生效缓存（秒级生效，免重启）。"""
    svc = get_quota_service()
    svc.set_daily_limit(db, body.daily_quota_limit)
    return QuotaSettings(daily_limit=svc.daily_limit())
