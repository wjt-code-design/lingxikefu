"""Admin 路由（BU-09 填充）：/api/v1/admin/users|stats（真实查询，禁空壳）。"""
from __future__ import annotations

from uuid import UUID

import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.core.database import get_db
from app.models.feedback import Feedback, FeedbackRating
from app.models.knowledge import Document
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.user import User, UserRole
from app.schemas.admin import AdminStats, HotGap, RoleUpdateReq, UserItem, UserListResp
from app.schemas.knowledge import OkResp

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=UserListResp)
def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserListResp:
    """分页列出租户内用户（account 取 email/phone 兜底）。"""
    tenant = settings.TENANT_DEFAULT
    total = db.scalar(select(func.count(User.id)).where(User.tenant_id == tenant)) or 0
    rows = (
        db.scalars(
            select(User)
            .where(User.tenant_id == tenant)
            .order_by(User.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .all()
    )
    items = [
        UserItem(
            user_id=str(u.id),
            account=u.email or u.phone or str(u.id),
            role=u.role.value,
            created_at=u.created_at.isoformat(),
        )
        for u in rows
    ]
    return UserListResp(items=items, total=total)


@router.put("/users/{user_id}/role", response_model=OkResp)
def update_user_role(
    user_id: UUID,
    req: RoleUpdateReq,
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OkResp:
    """变更用户角色（仅限同租户用户）。"""
    u = db.get(User, user_id)
    if u is None or u.tenant_id != settings.TENANT_DEFAULT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    u.role = UserRole(req.role)
    db.commit()
    return OkResp()


@router.get("/stats", response_model=AdminStats)
def get_stats(
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminStats:
    """运营统计（会话/消息/文档/赞踩计数；首字时延尚未埋点）。"""
    tenant = settings.TENANT_DEFAULT
    sessions = db.scalar(select(func.count(Session.id)).where(Session.tenant_id == tenant)) or 0
    messages = db.scalar(select(func.count(Message.id)).where(Message.tenant_id == tenant)) or 0
    documents = db.scalar(select(func.count(Document.id)).where(Document.tenant_id == tenant)) or 0
    feedback_up = (
        db.scalar(
            select(func.count(Feedback.id)).where(
                Feedback.tenant_id == tenant, Feedback.rating == FeedbackRating.up
            )
        )
        or 0
    )
    feedback_down = (
        db.scalar(
            select(func.count(Feedback.id)).where(
                Feedback.tenant_id == tenant, Feedback.rating == FeedbackRating.down
            )
        )
        or 0
    )
    # R-3：首字时延真实均值（assistant 消息 meta.first_token_ms 埋点，见 chat.py）。
    # 数据量小（统计面板），Python 侧聚合避免跨方言 JSON 查询差异。
    metas = db.scalars(
        select(Message.meta).where(
            Message.tenant_id == tenant, Message.role == MessageRole.assistant
        )
    ).all()
    latencies = [
        float(m["first_token_ms"]) for m in metas if isinstance(m, dict) and m.get("first_token_ms") is not None
    ]
    avg_first_token_ms = round(sum(latencies) / len(latencies), 1) if latencies else 0.0
    # F1：待补录问题 Top10——聚合 handoff/refuse 意图的用户消息（KB 未覆盖 → 运营补录信号）。
    # 问句归一化（去空白/全半角/标点）后分组，防"同一问题不同问法"重复计数；展示出现最多的原始问句。
    raw_rows = db.scalars(
        select(Message.content).where(
            Message.tenant_id == tenant,
            Message.role == MessageRole.user,
            Message.intent.in_(["handoff", "refuse"]),
        )
    ).all()

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s)  # 全角→半角
        # 去空白与全半角标点（re.escape 字面量拼接，避免 raw string 引号/转义歧义）
        return re.sub(f"[{re.escape(' \t\n\r，。？！、；：\"\'（）【】,.?!;:()[]')}]", "", s)

    groups: dict[str, dict] = {}
    for content in raw_rows:
        key = _norm(content)
        g = groups.setdefault(key, {"variants": {}})
        g["variants"][content] = g["variants"].get(content, 0) + 1
    hot_gaps = []
    for g in sorted(groups.values(), key=lambda x: -sum(x["variants"].values()))[:10]:
        # 展示组内出现最多的原始问句；次数平局取较短（更简洁、利于一眼看懂）
        question = max(g["variants"], key=lambda k: (g["variants"][k], -len(k)))
        hot_gaps.append(HotGap(question=question, count=sum(g["variants"].values())))
    return AdminStats(
        sessions=sessions,
        messages=messages,
        documents=documents,
        feedback_up=feedback_up,
        feedback_down=feedback_down,
        avg_first_token_ms=avg_first_token_ms,
        hot_gaps=hot_gaps,
    )


class FeedbackItem(BaseModel):
    message_content: str
    role: str
    comment: str | None = None
    created_at: str


class FeedbackListResp(BaseModel):
    items: list[FeedbackItem]
    total: int


@router.get("/feedback", response_model=FeedbackListResp)
def list_feedback(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> FeedbackListResp:
    """运营反馈列表：只看"踩"（down），join 消息内容（问题/回答），供运营判断补录/优化点。"""
    tenant = settings.TENANT_DEFAULT
    total = db.scalar(
        select(func.count(Feedback.id)).where(
            Feedback.tenant_id == tenant, Feedback.rating == FeedbackRating.down
        )
    ) or 0
    rows = db.execute(
        select(Message.content, Message.role, Feedback.comment, Feedback.created_at)
        .join(Feedback, Feedback.message_id == Message.id)
        .where(Feedback.tenant_id == tenant, Feedback.rating == FeedbackRating.down)
        .order_by(Feedback.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    items = [
        FeedbackItem(
            message_content=r[0] or "",
            role=r[1].value if hasattr(r[1], "value") else str(r[1]),
            comment=r[2],
            created_at=r[3].isoformat(),
        )
        for r in rows
    ]
    return FeedbackListResp(items=items, total=total)
