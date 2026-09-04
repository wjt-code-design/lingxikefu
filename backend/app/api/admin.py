"""Admin 路由（BU-09 填充）：/api/v1/admin/users|stats（真实查询，禁空壳）。"""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
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
    FeedbackGap,
    HotGap,
    IntentShadowBucket,
    IntentShadowDailyBucket,
    IntentShadowStats,
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
    keyword: str | None = Query(None, max_length=100, description="邮箱/手机号模糊搜索"),
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> UserListResp:
    """分页列出租户内用户（account 取 email/phone 兜底）；keyword 模糊搜邮箱/手机号（UI 审查中7）。"""
    tenant = settings.TENANT_DEFAULT
    cond = [User.tenant_id == tenant]
    if keyword:
        kw = f"%{keyword.strip()}%"
        cond.append(or_(User.email.ilike(kw), User.phone.ilike(kw)))
    total = db.scalar(select(func.count(User.id)).where(*cond)) or 0
    rows = (
        db.scalars(
            select(User)
            .where(*cond)
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
    days: int = Query(7, ge=0, le=365),
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> AdminStats:
    """运营统计（会话/消息/文档/赞踩计数 + R-3 首字时延均值 + F1 待补录问题 Top10）。

    三期 1：hot_gaps/feedback_gaps 支持 ``?days`` 信号时间窗（默认 7 天，0=不限——
    SQL 不带时间条件，输出与旧版逐字段一致）。时间窗**只作用于两个聚类字段**，
    refuse_count 等其余字段仍为全量口径。feedback_gaps：down 反馈连被踩消息
    原文聚类 Top10（问题原文/次数/最近 down 时间），与 hot_gaps（refuse 源）互补。
    """
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
    # 三期 1：信号时间窗（默认近 7 天；0=不限 → 不加时间条件，SQL 与旧版完全一致）。
    # UTC now 对齐 trend 端点先例（get_stats_trend）；created_at 列 timezone=True，
    # PG 按 timestamptz 比较，SQLite 存储层做一致的字符串渲染（既有 trend 测试锁定）。
    since = datetime.now(UTC) - timedelta(days=days) if days > 0 else None
    # F1：待补录问题 Top10——仅聚合 refuse 意图的用户消息（QA 检索但无依据被拒答 → KB 未覆盖
    # 的真正补录信号）。handoff（转人工/情绪）是正常分流、非知识缺口：补录知识解决不了"转人工"
    # 诉求，收进来只会污染运维补录清单（此前误把 handoff 一并统计，见修复备注）。
    # SQL 先按原文 GROUP BY 压缩行数（同问句一行，count 由数据库算），Python 仅做跨变体
    # 归一化归并（NFKC/去标点）——传输量从"消息数"降到"不同问句数"。
    gap_where = [
        Message.tenant_id == tenant,
        Message.role == MessageRole.user,
        Message.intent == "refuse",
    ]
    if since is not None:
        gap_where.append(Message.created_at >= since)
    raw_rows = db.execute(
        select(Message.content, func.count(Message.id))
        .where(*gap_where)
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

    # 三期 1：点踩缺口 Top10——down 反馈连被踩消息原文（join 同 /admin/feedback 先例：
    # Feedback.message_id == Message.id），归一化归并复用 hot_gaps 的 _norm 手法；
    # count=组内 down 反馈次数，last_at=组内最近一次 down 反馈时间（func.max 继承
    # 列类型，结果处理器返回 datetime，PG/SQLite 双兼容）。
    fb_where = [Feedback.tenant_id == tenant, Feedback.rating == FeedbackRating.down]
    if since is not None:
        fb_where.append(Feedback.created_at >= since)
    fb_rows = db.execute(
        select(Message.content, func.count(Feedback.id), func.max(Feedback.created_at))
        .join(Feedback, Feedback.message_id == Message.id)
        .where(*fb_where)
        .group_by(Message.content)
    ).all()
    fb_groups: dict[str, dict] = {}
    for content, cnt, last in fb_rows:
        g = fb_groups.setdefault(_norm(content), {"variants": {}, "last": None})
        g["variants"][content] = int(cnt)
        if last is not None and (g["last"] is None or last > g["last"]):
            g["last"] = last
    feedback_gaps = [
        FeedbackGap(
            question=max(g["variants"], key=lambda k: (g["variants"][k], -len(k))),
            count=sum(g["variants"].values()),
            last_at=g["last"].isoformat() if g["last"] else "",
        )
        for g in sorted(fb_groups.values(), key=lambda x: -sum(x["variants"].values()))[:10]
    ]

    # T1.2：运营观测聚合——工具分布 / 澄清轮数 / 会话主题分布 + 拒答口径。
    # JSON 索引由 SQLAlchemy 按方言编译（PG: ->>，SQLite: json_extract），同 R-3 latency 先例；
    # 拒答口径（大扫查 2026-08-25 修正）：澄清轮 rag_service emit refuse=False → intent 落 'qa'，
    # 天然不进 refuse_count——**refuse_count 即真拒答轮数**，勿再减 clarify_rounds（会双重扣减）；
    # clarify_rounds 是独立的澄清观测口径，两者无推导关系（hot_gaps 口径不受影响）。
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
        feedback_gaps=feedback_gaps,
        tool_dist=tool_dist,
        clarify_rounds=clarify_rounds,
        topic_dist=topic_dist,
        refuse_count=refuse_count,
    )


@router.get("/intent-shadow/stats", response_model=IntentShadowStats)
def get_intent_shadow_stats(
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
    since: str | None = Query(
        None,
        max_length=10,
        description="按日截断（YYYY-MM-DD，含当日）：只统计 created_at 本地日 >= since 的影子样本",
    ),
) -> IntentShadowStats:
    """LLM 意图分类影子一致率（架构二期 3，ADR-1 第一步：只记不驱动的验证数据）。

    聚合用户消息 ``meta["intent_shadow"]["intent"]``（LLM 影子产出）vs
    ``Message.intent``（规则式 classify_intent，单一真源）→
    ``{total, agree, agree_rate, by_intent}``。影子仅采样 qa 类消息，故常态只有
    qa 桶；出现其他桶 = 写入方异常信号，按原样透出不隐藏。
    JSON 路径由 SQLAlchemy 按方言编译（PG: meta->'intent_shadow'->>'intent'，
    SQLite: json_extract），同 R-3 latency 先例；GROUP BY 两列后 Python 侧只归并
    ≤N² 行，不做全量拉取。

    P5（2026-09-04）：``since`` 按日截断——规则分类器修复（chitchat 残句复扫
    0b53412）之前的影子分歧是旧口径失真样本（回填脚本灌入的 8/15 批 agree 仅
    68%），永久拉低总体使切换门槛（≥95% + ≥500）永远达不到。决策窗口应只计
    修复后数据。比较复用 day_col 同款 ``cast(date(created_at), String)``——
    PG/SQLite 双方言安全（避开 timestamptz 与 naive date 的时区坑），且与
    daily 分桶口径天然一致（同为本地日）；ISO 日期串字典序=时间序。
    """
    since_bound: str | None = None
    if since is not None:
        try:
            since_bound = datetime.strptime(since, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400, detail="since 须为 YYYY-MM-DD 日期"
            ) from None
    tenant = settings.TENANT_DEFAULT
    llm_col = Message.meta["intent_shadow"]["intent"].as_string()
    rule_col = sa.func.coalesce(Message.intent, "unknown")  # 旧数据 intent 可空
    day_col = sa.func.cast(sa.func.date(Message.created_at), sa.String)
    conds = [
        Message.tenant_id == tenant,
        Message.role == MessageRole.user,
        llm_col.isnot(None),  # 影子键存在 = 有效样本（分母）
    ]
    if since_bound is not None:
        conds.append(day_col >= since_bound)
    rows = db.execute(
        select(rule_col, llm_col, func.count(Message.id))
        .where(*conds)
        .group_by(rule_col, llm_col)
    ).all()

    total = 0
    agree = 0
    buckets: dict[str, list[int]] = {}
    for rule_intent, llm_intent, cnt in rows:
        cnt = int(cnt)
        total += cnt
        bucket = buckets.setdefault(str(rule_intent), [0, 0])
        bucket[0] += cnt
        if llm_intent is not None and str(rule_intent) == llm_intent:
            agree += cnt
            bucket[1] += cnt

    def _bucket(pair: list[int]) -> IntentShadowBucket:
        t, a = pair
        return IntentShadowBucket(
            total=t, agree=a, agree_rate=round(a / t, 4) if t else 0.0
        )

    min_total = settings.INTENT_SHADOW_MIN_TOTAL
    # 按日分桶（批次 I：双周观测留档——「连续两周无回归」的度量基础）。
    # func.date 跨方言（PG: date(timestamptz) / SQLite: date(text)）；日期升序输出。
    # P5：复用同一 conds——daily 与总体聚合同口径（since 截断同步生效，
    # 否则「总体按窗口算、daily 却混入窗口外旧桶」自相矛盾）。
    day_rows = db.execute(
        select(day_col, rule_col, llm_col, func.count(Message.id))
        .where(*conds)
        .group_by(day_col, rule_col, llm_col)
    ).all()
    daily_acc: dict[str, list[int]] = {}
    for day, rule_intent, llm_intent, cnt in day_rows:
        if day is None:
            continue
        acc = daily_acc.setdefault(str(day), [0, 0])
        acc[0] += int(cnt)
        if llm_intent is not None and str(rule_intent) == llm_intent:
            acc[1] += int(cnt)
    daily = [
        IntentShadowDailyBucket(
            date=d, total=t, agree=a, agree_rate=round(a / t, 4) if t else 0.0
        )
        for d, (t, a) in sorted(daily_acc.items())
    ]
    return IntentShadowStats(
        total=total,
        agree=agree,
        agree_rate=round(agree / total, 4) if total else 0.0,
        by_intent={k: _bucket(v) for k, v in buckets.items()},
        min_total=min_total,
        remaining=max(0, min_total - total),
        daily=daily,
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


@router.get("/stats/trend", response_model=StatsTrendResp)
def get_stats_trend(
    days: int = Query(14, ge=7, le=90),
    _: dict = Depends(require_admin),
    db: OrmSession = Depends(get_db),
) -> StatsTrendResp:
    """运营趋势（P1）：近 N 天会话/消息/工单按日计数。

    - P2-⑧：SQL 聚合（按日分组，PG/SQLite 双兼容）替代全量拉取 Python 分桶；
    - 无数据日期补 0，保证折线图连续。
    """
    tenant = settings.TENANT_DEFAULT
    since = datetime.now(UTC) - timedelta(days=days - 1)
    axis = [(since + timedelta(days=i)).date().isoformat() for i in range(days)]

    def _day(col: Any) -> Any:
        """'YYYY-MM-DD' 分组表达式（cast to text 再取前 10 位）。

        P2-⑧：`cast(col, Date)` 在 SQLite 走 NUMERIC 亲和返回非 str、Date 处理器
        fromisoformat 崩（实测 TypeError），PG 无内置 date() 函数——substr 方案双库兼容。
        """
        return sa.func.substr(sa.cast(col, sa.String), 1, 10)

    # P2-⑧：SQL 聚合（按日分组）替代全量拉取 Python 分桶。
    # PG 方言坑（2026-08-31 线上 500）：select 与 group_by 各自独立调用 _day() 会
    # 生成两组 bind 参数（substr_2/3 vs substr_4/5），PG 解析期无法认定二者同源 →
    # GroupingError（SQLite 按文本匹配照常通过，本地全量绿掩盖线上红）。
    # 故每列的 _day 表达式只构造一次，select/group_by 共用同一实例。
    day_s = _day(Session.created_at)
    s_rows = db.execute(
        select(day_s, func.count())
        .where(Session.tenant_id == tenant, Session.created_at >= since)
        .group_by(day_s)
    ).all()
    day_m = _day(Message.created_at)
    m_rows = db.execute(
        select(day_m, func.count())
        .where(Message.tenant_id == tenant, Message.created_at >= since)
        .group_by(day_m)
    ).all()
    day_t = _day(Ticket.created_at)
    t_rows = db.execute(
        select(day_t, func.count())
        .where(Ticket.tenant_id == tenant, Ticket.created_at >= since)
        .group_by(day_t)
    ).all()

    # T1.3：工具回答按 (日, tool) 分组 + 澄清轮按日分组（口径与 stats 聚合一致：
    # 仅 assistant、tool 非空串；clarify 为 meta.clarify=True）。day_m 复用上例实例。
    tool_col = Message.meta["tool"].as_string()
    tool_rows = db.execute(
        select(day_m, tool_col, func.count())
        .where(
            Message.tenant_id == tenant,
            Message.role == MessageRole.assistant,
            Message.created_at >= since,
            tool_col.isnot(None),
        )
        .group_by(day_m, tool_col)
    ).all()
    clarify_col = Message.meta["clarify"].as_boolean()
    c_rows = db.execute(
        select(day_m, func.count())
        .where(
            Message.tenant_id == tenant,
            Message.role == MessageRole.assistant,
            Message.created_at >= since,
            clarify_col.is_(True),
        )
        .group_by(day_m)
    ).all()

    def bucket(rows) -> dict[str, int]:
        return {k: int(c) for k, c in rows}

    sb, mb, tb = bucket(s_rows), bucket(m_rows), bucket(t_rows)

    # 按日聚合工具分布：{date: {tool: count}}，空串工具名不计
    tool_by_day: dict[str, dict[str, int]] = {}
    for day, tool, cnt in tool_rows:
        if not tool:
            continue
        k = str(day)  # _day 表达式已产出 'YYYY-MM-DD'
        tool_by_day.setdefault(k, {})
        tool_by_day[k][tool] = tool_by_day[k].get(tool, 0) + int(cnt)
    cb = bucket(c_rows)

    return StatsTrendResp(
        days=[
            TrendPoint(
                date=d,
                sessions=sb.get(d, 0),
                messages=mb.get(d, 0),
                tickets=tb.get(d, 0),
                tool_dist=tool_by_day.get(d, {}),
                clarify_rounds=cb.get(d, 0),
            )
            for d in axis
        ]
    )
