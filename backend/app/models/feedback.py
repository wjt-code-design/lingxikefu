"""反馈模型（BU-07 填充业务逻辑）。"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class FeedbackRating(StrEnum):
    up = "up"
    down = "down"


class SuggestionType(StrEnum):
    """用户意见反馈类型（意见反馈页：问题反馈 / 功能建议 / 其他）。"""
    bug = "bug"
    suggestion = "suggestion"
    other = "other"


class Suggestion(Base):
    """用户意见反馈（P2-修复#2）：FeedbackPage 提交的整页建议，区别于消息级赞踩 Feedback。"""

    __tablename__ = "suggestions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[SuggestionType] = mapped_column(
        sa.Enum(SuggestionType, name="suggestion_type"),
        nullable=False,
        default=SuggestionType.suggestion,
    )
    content: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    contact: Mapped[str | None] = mapped_column(sa.String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    message_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rating: Mapped[FeedbackRating] = mapped_column(
        sa.Enum(FeedbackRating, name="feedback_rating"),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
