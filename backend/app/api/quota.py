"""Quota 路由（BU-08）：GET /api/v1/quota —— 当前用户今日配额。"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.schemas.quota import QuotaResp
from app.services.quota import get_quota_service

router = APIRouter(prefix="/quota", tags=["quota"])


@router.get("", response_model=QuotaResp)
def get_quota(payload: dict = Depends(get_current_user)) -> QuotaResp:
    qs = get_quota_service()
    used = qs.used_today(payload["sub"])
    limit = qs.daily_limit(guest=bool(payload.get("guest")))
    return QuotaResp(
        date=date.today().isoformat(),
        used=used,
        limit=limit,
        left=max(0, limit - used),
    )
