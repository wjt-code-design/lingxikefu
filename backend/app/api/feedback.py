"""Feedback 路由（BU-07）：POST /api/v1/messages/{message_id}/feedback。

对 assistant 消息提交 up/down 反馈（可带评语）。同一用户对同一消息重复提交 = 更新（幂等）。
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.feedback import Feedback, FeedbackRating
from app.models.message import Message
from app.models.session import Session
from app.services.user_profile_service import merge_profile

router = APIRouter(prefix="/messages", tags=["feedback"])
logger = logging.getLogger(__name__)


class FeedbackReq(BaseModel):
    rating: FeedbackRating
    comment: str | None = Field(default=None, max_length=500)


class FeedbackResp(BaseModel):
    ok: bool = True


@router.post("/{message_id}/feedback", response_model=FeedbackResp)
def submit_feedback(
    message_id: uuid.UUID,
    req: FeedbackReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> FeedbackResp:
    """提交/更新反馈。消息须存在且属于当前用户（防越权评价他人消息）。"""
    user_id = uuid.UUID(payload["sub"])

    msg = db.scalar(select(Message).where(Message.id == message_id))
    if not msg:
        raise HTTPException(status_code=404, detail="message not found")
    s = db.scalar(select(Session).where(Session.id == msg.session_id))
    if not s or s.user_id != user_id:
        raise HTTPException(status_code=403, detail="无权操作该消息")

    fb = db.scalar(
        select(Feedback).where(Feedback.message_id == message_id, Feedback.user_id == user_id)
    )
    if fb:
        fb.rating = req.rating
        fb.comment = req.comment
    else:
        db.add(
            Feedback(
                tenant_id=settings.TENANT_DEFAULT,
                message_id=message_id,
                user_id=user_id,
                rating=req.rating,
                comment=req.comment,
            )
        )
    db.commit()
    # 2026-08-22 Phase B：满意度入画像（幂等键=消息+评分，同消息同评分只计一次；
    # 用户改评（up→down）按真实态度各计一次）。fail-open 不影响反馈主流程。
    try:
        merge_profile(
            db,
            user_id,
            "",
            sat_rating=req.rating.value,
            idem_key=f"fb:{message_id}:{req.rating.value}",
        )
    except Exception:  # noqa: BLE001 - 采集兜底
        logger.exception("满意度采集异常（不影响反馈）")
    return FeedbackResp()
