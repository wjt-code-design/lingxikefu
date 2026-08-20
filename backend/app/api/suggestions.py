"""Suggestion 路由（P2-修复#2）：意见反馈真实落库。

- POST /api/v1/suggestions：任意登录用户提交（防刷限流：每用户 5 分钟 5 条）；
- GET /api/v1/suggestions：admin 分页查看（运营跟进用户建议）。
此前前端意见反馈页是假提交（setTimeout + toast），用户反馈全部丢弃——本路由补齐闭环。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user, require_admin
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.models.feedback import Suggestion, SuggestionType
from app.models.user import User

router = APIRouter(prefix="/suggestions", tags=["suggestions"])

#: 防刷限流：每用户 5 分钟最多 5 条（按 user 维度，绕 IP 换号无效）
_SUBMIT_LIMIT = 5
_SUBMIT_WINDOW = 300


class SuggestionReq(BaseModel):
    type: SuggestionType = SuggestionType.suggestion
    content: str = Field(min_length=1, max_length=1000)
    contact: str | None = Field(default=None, max_length=255)


class OkResp(BaseModel):
    ok: bool = True


class SuggestionItem(BaseModel):
    id: str
    user_account: str | None  # 提交人 email/phone（运营联系用）
    type: SuggestionType
    content: str
    contact: str | None
    created_at: str


class SuggestionListResp(BaseModel):
    items: list[SuggestionItem]
    total: int


@router.post("", response_model=OkResp, status_code=status.HTTP_201_CREATED)
def submit_suggestion(
    req: SuggestionReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """提交意见反馈（登录用户）。限流按用户维度计数，防单用户刷库。"""
    user_id = payload["sub"]
    if not rate_limit(f"ratelimit:suggestion:{user_id}", _SUBMIT_LIMIT, _SUBMIT_WINDOW):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "提交过于频繁，请稍后再试")
    db.add(
        Suggestion(
            tenant_id=settings.TENANT_DEFAULT,
            user_id=uuid.UUID(user_id),
            type=req.type,
            content=req.content.strip(),
            contact=req.contact,
        )
    )
    db.commit()
    return OkResp()


@router.get("", response_model=SuggestionListResp)
def list_suggestions(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> SuggestionListResp:
    """意见反馈列表（admin）：时间倒序分页，附提交人账号（email/phone）。"""
    cond = Suggestion.tenant_id == settings.TENANT_DEFAULT
    total = db.scalar(select(func.count(Suggestion.id)).where(cond)) or 0
    rows = db.execute(
        select(Suggestion, User)
        .join(User, Suggestion.user_id == User.id)
        .where(cond)
        .order_by(Suggestion.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    items = [
        SuggestionItem(
            id=str(s.id),
            user_account=u.email or u.phone,
            type=s.type,
            content=s.content,
            contact=s.contact,
            created_at=s.created_at.isoformat(),
        )
        for s, u in rows
    ]
    return SuggestionListResp(items=items, total=total)
