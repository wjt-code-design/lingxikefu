"""Admin 路由（BU-09 填充）：/api/v1/admin/users|stats（真实查询，禁空壳）。"""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import require_admin
from app.core.config import settings
from app.core.database import get_db
from app.models.feedback import Feedback, FeedbackRating
from app.models.knowledge import Document
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.ticket import Ticket
from app.models.user import User, UserRole
from app.schemas.admin import (
    AdminStats,
    HotGap,
    RoleUpdateReq,
    StatsTrendResp,
    TrendPoint,
    UserItem,
    UserListResp,
)
from app.schemas.knowledge import OkResp
from app.services.audit_service import audit_log

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=UserListResp)
def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
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
    payload: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """变更用户角色（仅限同租户用户）。

    BUG-04 自保护：
    - admin 禁止修改自己的角色（单 admin 系统自我降级即锁死，无法再进管理后台）；
    - 多 admin 场景也禁止降级最后一个 admin（防御：目标为 admin 且系统仅剩一个时拒绝）。
    """
    if str(user_id) == payload["sub"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不允许修改自己的角色")
    u = db.get(User, user_id)
    if u is None or u.tenant_id != settings.TENANT_DEFAULT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if u.role == UserRole.admin and req.role != UserRole.admin:
        admin_cnt = (
            db.scalar(
                select(func.count(User.id)).where(
                    User.tenant_id == settings.TENANT_DEFAULT,
                    User.role == UserRole.admin,
                )
            )
            or 0
        )
        if admin_cnt <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能降级最后一个管理员")
    u.role = UserRole(req.role)
    db.commit()
    # Phase4 审计埋点：用户角色变更（user.role，detail=新角色）
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="user.role",
        resource="user",
        resource_id=str(user_id),
        detail=req.role,
    )
    return OkResp()


@router.get("/stats", response_model=AdminStats)
def get_stats(
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> AdminStats:
    """运营统计（会话/消息/文档/赞踩计数 + R-3 首字时延均值 + F1 待补录问题 Top10）。"""
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
    # R-3：首字时延均值 SQL 聚合（PG: meta->>'first_token_ms'，SQLite: json_extract）。
    # 此前全量拉 assistant meta 到 Python 内存聚合 O(N)，消息量增长后统计面板变慢。
    latency_col = Message.meta["first_token_ms"].as_float()
    avg_row = db.execute(
        select(func.avg(latency_col), func.count(latency_col)).where(
            Message.tenant_id == tenant, Message.role == MessageRole.assistant
        )
    ).one()
    avg_first_token_ms = round(float(avg_row[0]), 1) if avg_row[0] is not None else 0.0
    # F1：待补录问题 Top10——仅聚合 refuse 意图的用户消息（QA 检索但无依据被拒答 → KB 未覆盖
    # 的真正补录信号）。handoff（转人工/情绪）是正常分流、非知识缺口：补录知识解决不了"转人工"
    # 诉求，收进来只会污染运维补录清单（此前误把 handoff 一并统计，见修复备注）。
    # SQL 先按原文 GROUP BY 压缩行数（同问句一行，count 由数据库算），Python 仅做跨变体
    # 归一化归并（NFKC/去标点）——传输量从"消息数"降到"不同问句数"。
    raw_rows = db.execute(
        select(Message.content, func.count(Message.id))
        .where(
            Message.tenant_id == tenant,
            Message.role == MessageRole.user,
            Message.intent == "refuse",
        )
        .group_by(Message.content)
    ).all()

    # 去空白与全半角标点：字符类提为普通字符串常量（f-string 表达式内含反斜杠是
    # py3.12+ 语法，本项目 target py311 不允许）
    _punct = " \t\n\r，。？！、；：\"'（）【】,.?!;:()[]"

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKC", s)  # 全角→半角
        return re.sub(f"[{re.escape(_punct)}]", "", s)

    groups: dict[str, dict] = {}
    for content, cnt in raw_rows:
        key = _norm(content)
        g = groups.setdefault(key, {"variants": {}})
        g["variants"][content] = cnt
    hot_gaps = []
    for g in sorted(groups.values(), key=lambda x: -sum(x["variants"].values()))[:10]:
        # 展示组内出现最多的原始问句；次数平局取较短（更简洁、利于一眼看懂）
        question = max(g["variants"], key=lambda k: (g["variants"][k], -len(k)))
        hot_gaps.append(HotGap(question=question, count=sum(g["variants"].values())))

    # T1.2：运营观测聚合——工具分布 / 澄清轮数 / 会话主题分布 + 拒答口径。
    # JSON 索引由 SQLAlchemy 按方言编译（PG: ->>，SQLite: json_extract），同 R-3 latency 先例；
    # 不变式：每澄清轮恰对应一个 refuse 用户消息 → 真拒答轮数 = refuse_count - clarify_rounds，
    # 两口径分开暴露、由消费端推导（澄清问句本身也是知识缺口信号，hot_gaps 口径保持不变）。
    tool_col = Message.meta["tool"].as_string()
    tool_rows = db.execute(
        select(tool_col, func.count(Message.id)).where(
            Message.tenant_id == tenant,
            Message.role == MessageRole.assistant,
            tool_col.isnot(None),
        ).group_by(tool_col)
    ).all()
    tool_dist = {tool: cnt for tool, cnt in tool_rows if tool}
    clarify_col = Message.meta["clarify"].as_boolean()
    clarify_rounds = (
        db.scalar(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant,
                Message.role == MessageRole.assistant,
                clarify_col.is_(True),
            )
        )
        or 0
    )
    topic_col = Session.conv_state["topic"].as_string()
    topic_rows = db.execute(
        select(topic_col, func.count(Session.id)).where(
            Session.tenant_id == tenant,
            topic_col.isnot(None),
        ).group_by(topic_col)
    ).all()
    topic_dist = {topic: cnt for topic, cnt in topic_rows if topic}
    refuse_count = (
        db.scalar(
            select(func.count(Message.id)).where(
                Message.tenant_id == tenant,
                Message.role == MessageRole.user,
                Message.intent == "refuse",
            )
        )
        or 0
    )
    return AdminStats(
        sessions=sessions,
        messages=messages,
        documents=documents,
        feedback_up=feedback_up,
        feedback_down=feedback_down,
        avg_first_token_ms=avg_first_token_ms,
        hot_gaps=hot_gaps,
        tool_dist=tool_dist,
        clarify_rounds=clarify_rounds,
        topic_dist=topic_dist,
        refuse_count=refuse_count,
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
    db: OrmSession = Depends(get_db),
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


def _day_key(dt) -> str:
    """统一日期键（兼容 sqlite naive / pg tz-aware）。"""
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone()
    return dt.date().isoformat()


@router.get("/stats/trend", response_model=StatsTrendResp)
def get_stats_trend(
    days: int = Query(14, ge=7, le=90),
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> StatsTrendResp:
    """运营趋势（P1）：近 N 天会话/消息/工单按日计数。

    - Python 侧聚合（数据量小，与 stats 的 latency 聚合同风格，兼容 sqlite/pg）；
    - 无数据日期补 0，保证折线图连续。
    """
    tenant = settings.TENANT_DEFAULT
    since = datetime.now(UTC) - timedelta(days=days - 1)
    axis = [(since + timedelta(days=i)).date().isoformat() for i in range(days)]

    s_dates = db.scalars(
        select(Session.created_at).where(Session.tenant_id == tenant, Session.created_at >= since)
    ).all()
    m_dates = db.scalars(
        select(Message.created_at).where(Message.tenant_id == tenant, Message.created_at >= since)
    ).all()
    t_dates = db.scalars(
        select(Ticket.created_at).where(Ticket.tenant_id == tenant, Ticket.created_at >= since)
    ).all()

    def bucket(rows) -> dict[str, int]:
        c: dict[str, int] = {}
        for r in rows:
            k = _day_key(r)
            c[k] = c.get(k, 0) + 1
        return c

    sb, mb, tb = bucket(s_dates), bucket(m_dates), bucket(t_dates)
    return StatsTrendResp(
        days=[
            TrendPoint(
                date=d,
                sessions=sb.get(d, 0),
                messages=mb.get(d, 0),
                tickets=tb.get(d, 0),
            )
            for d in axis
        ]
    )
