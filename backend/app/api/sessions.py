"""Sessions 路由（BU-03）：/api/v1/sessions 创建 / 列表 / 详情。

- 会话按当前用户隔离（user_id 来自 token payload.sub）；
- chat 依赖会话存在性校验（chat/stream 会查 session 归属）。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy import or_ as sa_or
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user, require_roles
from app.core.config import settings
from app.core.database import get_db
from app.llm_clients.chat import get_chat_client
from app.models.message import Message, MessageRole, MessageSource
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User
from app.prompts.agent_assist_prompt import build_assist_messages
from app.services import conversation_state
from app.services.audit_service import audit_log
from app.services.kb_lookup import doc_titles as _doc_titles_sync
from app.services.kb_lookup import get_latest_kb_id as _latest_kb_id
from app.services.notification_service import create_notification
from app.services.retrieval_service import search_kb
from app.services.session_context import build_handoff_summary
from app.services.ticket_automation import auto_start_processing
from app.services.user_profile_service import get_profile as get_user_profile

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])


class OkResp(BaseModel):
    ok: bool = True


class CreateSessionReq(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class SessionItem(BaseModel):
    session_id: str
    title: str | None
    created_at: str
    updated_at: str  # L2：补齐 updated_at，与前端契约对齐
    satisfaction: str | None = None  # BUG-06：会话满意度（satisfied/neutral/unsatisfied）
    # BUG-12：客户标识（agent/admin 全租户视角用于区分客户；user 视角为 None 不泄露他人信息）
    user_email: str | None = None
    user_phone: str | None = None


class SessionMessageSource(BaseModel):
    """消息引用来源（对齐契约 MessageSource：chunk_id/doc_id/doc_title/snippet/score）。
    2026-08-21：会话详情补曝光引用来源，修复历史消息无溯源（此前详情接口不含 sources）。"""

    chunk_id: str
    doc_id: str | None = None
    doc_title: str
    snippet: str
    score: float


class SessionMessage(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    intent: str | None = None  # BUG-07：消息意图（qa/handoff/chitchat）
    # Branch 3：人工客服归属（契约 Message.agent_id / agent_name，仅 role=agent 携带）
    agent_id: str | None = None
    agent_name: str | None = None
    # 大扫查 F-major（2026-08-25）：工具回答标记透出（meta.tool）——历史加载/observe
    # 视角气泡徽章的读路径（写路径 chat.py 落库、直播态走 SSE done.tool）
    tool: str | None = None
    # 2026-08-21：AI 回复的引用来源（assistant 角色）；user/agent 通常为空
    sources: list[SessionMessageSource] = Field(default_factory=list)


class SessionDetail(BaseModel):
    """会话详情（含消息历史），供 agent 查看用户历史对话（M8）。"""
    id: str
    title: str | None
    messages: list[SessionMessage]
    # 2026-08-22 Phase D：用户画像摘要（仅 agent/admin 可见；顾客端为 None 不泄露他人画像）。
    profile: dict | None = None
    # 2026-08-22：转人工交接摘要（本次会话当前主题/实体/最近诉求；仅 agent/admin 可见）。
    handoff_summary: dict | None = None
    # 批次B：会话状态机（阶段+槽位；agent/admin 观察用，user 视角同返回——内容仅含
    # 用户自己会话的主题/订单号，无越权数据面，与 profile 的仅-staff 可见不同类）。
    conv_state: dict | None = None


class SessionListResp(BaseModel):
    items: list[SessionItem]
    total: int  # L2：真实总数（非 items.length 冒充）


@router.post("", response_model=SessionItem)
def create_session(
    req: CreateSessionReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> SessionItem:
    s = Session(
        tenant_id=settings.TENANT_DEFAULT,
        user_id=uuid.UUID(payload["sub"]),
        title=req.title,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return SessionItem(
        session_id=str(s.id),
        title=s.title,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
        satisfaction=s.satisfaction,
    )


@router.get("", response_model=SessionListResp)
def list_sessions(
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=100, description="标题/客户邮箱/电话模糊搜索"),
    satisfaction: str | None = Query(None, pattern="^(satisfied|neutral|unsatisfied)$"),
    order: str = Query("updated", pattern="^(updated|created)$", description="排序键：updated（默认）| created（审计页）"),
) -> SessionListResp:
    """会话列表（BUG-01 / BUG-05 + 第三批 #7 服务端过滤）。

    - user：只看自己的会话（Session.user_id == 当前用户）；
    - agent/admin：看全租户客户会话（客服工作台「会话列表」核心能力）；
    - 支持 page/size 分页（offset/limit），total 为过滤后的真实总数；
    - keyword / satisfaction 服务端过滤（此前审计页 size=100 客户端过滤，
      第 101 条会话静默不可见）；keyword 命中 标题/客户邮箱/电话（outerjoin User）；
    - 排序默认 updated_at desc（配合 BUG-03 的 session touch）；
      order=created 供审计页按创建时间倒序（保持原前端语义）。
    - BUG-12：agent/admin 视角返回每个会话所属客户标识（email/phone），
      供工作台历史面板区分客户；user 视角仅返回自己会话（标识为自己，不泄露他人）。
    """
    user_id = uuid.UUID(payload["sub"])
    role = payload.get("role")
    conds = [
        Session.tenant_id == settings.TENANT_DEFAULT
        if role in ("admin", "agent")
        else Session.user_id == user_id
    ]
    if satisfaction:
        conds.append(Session.satisfaction == satisfaction)
    if keyword:
        kw = f"%{keyword.strip()}%"
        conds.append(
            sa_or(
                Session.title.ilike(kw),
                User.email.ilike(kw),
                User.phone.ilike(kw),
            )
        )
    stmt = (
        select(Session)
        .outerjoin(User, Session.user_id == User.id)
        .where(*conds)
    )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order_col = Session.created_at if order == "created" else Session.updated_at
    rows = db.scalars(
        stmt.order_by(order_col.desc()).offset((page - 1) * size).limit(size)
    ).all()
    # BUG-12：批量取会话归属用户的 email/phone（避免 N+1 查询）
    user_ids = {s.user_id for s in rows}
    user_map: dict[uuid.UUID, User] = {}
    if user_ids:
        for u in db.scalars(select(User).where(User.id.in_(user_ids))).all():
            user_map[u.id] = u
    items: list[SessionItem] = []
    for s in rows:
        u = user_map.get(s.user_id)
        items.append(
            SessionItem(
                session_id=str(s.id),
                title=s.title,
                created_at=s.created_at.isoformat(),
                updated_at=s.updated_at.isoformat(),
                satisfaction=s.satisfaction,
                user_email=u.email if u else None,
                user_phone=u.phone if u else None,
            )
        )
    return SessionListResp(total=total, items=items)


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
    limit: int = Query(200, ge=1, le=1000, description="返回最新 N 条消息（升序）；审计页可传大值"),
) -> SessionDetail:
    user_id = uuid.UUID(payload["sub"])
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    # M3 + R-1：越权读防护 —— 仅会话所有者可访问；
    # agent/admin 可读任意用户会话（客服查看用户历史对话场景，M8）。
    role = payload.get("role")
    if s.user_id != user_id and role not in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="forbidden")
    # 第三批 #8：超长会话防全量加载——取最新 limit 条（desc + limit）再反转为升序时间线。
    # 聊天历史/轮询均依赖"最新优先"语义（最新 agent 消息必含在内）；超限的旧消息不返回。
    msgs = list(
        reversed(
            db.scalars(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            ).all()
        )
    )
    # 2026-08-21：批量取这些消息的引用来源（message_sources），按 message_id 分组。
    # 修复历史消息无溯源：此前详情接口遗漏 sources，前端历史气泡/溯源面板恒空。
    src_by_msg: dict[str, list[dict]] = {}
    msg_ids = [m.id for m in msgs]
    if msg_ids:
        for src in db.scalars(
            select(MessageSource).where(MessageSource.message_id.in_(msg_ids))
        ).all():
            src_by_msg.setdefault(str(src.message_id), []).append(
                {
                    "chunk_id": str(src.chunk_id),
                    "doc_id": str(src.doc_id),
                    "doc_title": src.doc_title,
                    "snippet": src.snippet,
                    "score": float(src.score),
                }
            )
    # 2026-08-22 Phase D：客服侧展示画像——仅 agent/admin 返回（读会话 owner 画像；
    # 顾客端 None 不泄露；fail-open：读取异常 → None，不影响会话详情主流程）。
    profile: dict | None = None
    # 转人工交接摘要（本次会话上下文压缩打包）：由消息历史规则聚合，仅 agent/admin 展示。
    handoff_summary: dict | None = None
    if role in ("admin", "agent"):
        try:
            profile = get_user_profile(db, s.user_id)
        except Exception:  # noqa: BLE001 - fail-open
            profile = None
        try:
            handoff_summary = build_handoff_summary(
                [{"role": m.role.value, "content": m.content} for m in msgs],
                conv_state=s.conv_state,
            )
        except Exception:  # noqa: BLE001 - fail-open
            handoff_summary = None
    return SessionDetail(
        id=str(s.id),
        title=s.title,
        profile=profile,
        handoff_summary=handoff_summary,
        conv_state=s.conv_state,
        messages=[
            SessionMessage(
                id=str(m.id),
                role=m.role.value,
                content=m.content,
                created_at=m.created_at.isoformat(),
                intent=m.intent,  # BUG-07：返回真实意图供客服判断转人工
                agent_id=m.agent_id,  # Branch 3：人工客服归属透出
                agent_name=m.agent_name,
                tool=(m.meta or {}).get("tool"),  # 大扫查 F-major：工具标记读路径
                sources=src_by_msg.get(str(m.id), []),
            )
            for m in msgs
        ],
    )


class AgentMessageReq(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


@router.post("/{session_id}/messages", response_model=SessionMessage, status_code=201)
def post_agent_message(
    session_id: uuid.UUID,
    body: AgentMessageReq,
    payload: dict = Depends(require_roles("admin", "agent")),
    db: OrmSession = Depends(get_db),
) -> SessionMessage:
    """人工客服代发消息（Branch 3）：仅 admin/agent 可写。

    - 落库 role='agent'（契约 P2），顾客端刷新/轮询即可看到——替代"仅本地模拟"，
      刷新不丢、顾客端真实可见；
    - 写后 touch session.updated_at → 会话在列表/工作台排序提前；
    - 归属：agent_id=操作人 sub，agent_name=操作人 email/phone（无则「人工客服」）。
    """
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="empty content")
    staff = db.scalar(select(User).where(User.id == uuid.UUID(payload["sub"])))
    m = Message(
        tenant_id=settings.TENANT_DEFAULT,
        session_id=session_id,
        role=MessageRole.agent,
        content=content,
        agent_id=str(payload["sub"]),
        agent_name=(staff.email or staff.phone or "人工客服") if staff else "人工客服",
    )
    db.add(m)
    s.updated_at = datetime.now(UTC)  # touch：客服回复后会话排序提前
    # 自动化：客服首次发言 → open→processing
    try:
        auto_start_processing(db, session_id, uuid.UUID(payload["sub"]))
    except Exception:  # noqa: BLE001 - 自动化失败不阻塞主流程
        logger.exception("ticket_auto: auto_start_processing failed")
    db.commit()
    db.refresh(m)
    return SessionMessage(
        id=str(m.id),
        role=m.role.value,
        content=m.content,
        created_at=m.created_at.isoformat(),
        intent=m.intent,
        agent_id=m.agent_id,
        agent_name=m.agent_name,
    )


@router.delete("/{session_id}", response_model=OkResp)
def delete_session(
    session_id: uuid.UUID,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """删除会话（T4）：所有者或 admin；含未关闭工单的会话禁删（业务约束，防工单丢失溯源）。"""
    user_id = uuid.UUID(payload["sub"])
    role = payload.get("role")
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if s.user_id != user_id and role != "admin":
        raise HTTPException(status_code=404, detail="session not found")  # 防探测
    # 业务约束：含 open/processing 工单的会话禁删（工单需先流转关闭）
    active_ticket = db.scalar(
        select(Ticket).where(
            Ticket.session_id == session_id,
            Ticket.status.in_([TicketStatus.open, TicketStatus.processing]),
        )
    )
    if active_ticket:
        raise HTTPException(status_code=409, detail="会话存在未关闭工单，请先处理工单")
    db.delete(s)
    db.commit()
    # Phase4 审计埋点：删除会话（session.delete）
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="session.delete",
        resource="session",
        resource_id=str(session_id),
    )
    return OkResp()


class SatisfactionReq(BaseModel):
    rating: str = Field(..., pattern="^(satisfied|neutral|unsatisfied)$")


@router.post("/{session_id}/satisfaction", response_model=OkResp)
def rate_satisfaction(
    session_id: uuid.UUID,
    body: SatisfactionReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> OkResp:
    """会话级满意度（P2-2）：user 对整段会话评分（幂等覆盖，仅限自己的会话）。"""
    user_id = uuid.UUID(payload["sub"])
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s or s.user_id != user_id:
        raise HTTPException(status_code=404, detail="session not found")  # 防探测
    s.satisfaction = body.rating
    db.commit()
    # 通知中心：满意度提交 → 推给 admin（fail-open，不阻塞主流程）
    create_notification(
        db,
        recipient_role="admin",
        event_type="satisfaction.submitted",
        title="收到满意度评价",
        content=f"会话 {session_id} 用户评价：{body.rating}",
        resource_type="session",
        resource_id=str(session_id),
    )
    return OkResp()


# ===================== 坐席辅助（批次A，2026-08-24） =====================

class SuggestReq(BaseModel):
    """坐席辅助请求：question 缺省取会话最近一条顾客消息。"""

    question: str | None = Field(default=None, max_length=500)
    refresh: bool = False  # 绕过结果缓存强制重新生成（前端「重新生成」按钮）


class SuggestResp(BaseModel):
    """坐席辅助响应：草拟回复 + 引用来源。fail-open：失败返回空 text（不 5xx）。"""

    text: str = ""
    sources: list[SessionMessageSource] = Field(default_factory=list)


#: 60s 结果缓存：连点/重开不重复调 LLM（key=(session_id, question)；仅缓存成功结果，
#: 瞬时失败不粘滞）。线程锁 + 按值分桶，对齐 chat._kb_cache 模式（B4 同款纪律）。
_suggest_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_suggest_lock = threading.Lock()
_SUGGEST_CACHE_TTL = 60.0


def _doc_titles(db: OrmSession, doc_ids: set[str]) -> dict[str, str]:
    """文档标题查询（大扫查 O1：收敛 kb_lookup.doc_titles；str id 适配层保留给 suggest 调用）。"""
    if not doc_ids:
        return {}
    return _doc_titles_sync(db, {uuid.UUID(d) for d in doc_ids})


def _latest_user_message(db: OrmSession, session_id: uuid.UUID) -> str | None:
    """最近一条顾客消息（建议的默认对象）。"""
    m = db.scalar(
        select(Message)
        .where(Message.session_id == session_id, Message.role == MessageRole.user)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return m.content if m else None


@router.post("/{session_id}/suggest", response_model=SuggestResp)
async def suggest_reply(
    session_id: uuid.UUID,
    body: SuggestReq,
    payload: dict = Depends(require_roles("admin", "agent")),
    db: OrmSession = Depends(get_db),
) -> SuggestResp:
    """坐席辅助（批次A）：为人工客服草拟回复建议。手动触发、fail-open、60s 结果缓存。

    - 不走完整 RAG 管线：直接检索 top3（拒答场景更要给「需确认什么」的建议）；
    - 不扣用户配额（内部工具）；
    - LLM 用非流式 complete（客服点按钮等 1-2s 可接受，无逐字上屏需求）。
    """
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")

    question = (body.question or "").strip()
    if not question:
        question = (await run_in_threadpool(_latest_user_message, db, session_id) or "").strip()
    if not question:
        raise HTTPException(status_code=422, detail="no user message to suggest for")

    # 结果缓存命中直接返回（成功结果才有缓存条目）；refresh=True 跳过读取强制重新生成
    cache_key = (str(session_id), question)
    if not body.refresh:
        with _suggest_lock:
            hit = _suggest_cache.get(cache_key)
            if hit and time.time() - hit[0] < _SUGGEST_CACHE_TTL:
                return SuggestResp(**hit[1])

    try:
        kb_id = await run_in_threadpool(_latest_kb_id, db)
        if kb_id is None:
            return SuggestResp()  # 无知识库：空建议（fail-open，不缓存）

        chunks = await run_in_threadpool(search_kb, question, kb_id, 3)

        def _history() -> list[dict]:
            rows = (
                db.scalars(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.created_at.desc())
                    .limit(6)
                )
                .all()
            )
            return [{"role": m.role.value, "content": m.content} for m in reversed(rows)]

        history = await run_in_threadpool(_history)
        # 大扫查修复（M-1）：建议 prompt 并入会话状态——顾客已提供订单号时不再重复索要
        messages = build_assist_messages(
            question=question,
            history=history,
            chunks=chunks,
            state_hint=conversation_state.to_prompt_hint(s.conv_state),
        )
        text = (await get_chat_client().complete(messages)).strip()

        titles = await run_in_threadpool(
            _doc_titles, db, {c.doc_id for c in chunks if c.doc_id}
        )
        sources = [
            SessionMessageSource(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id or None,
                doc_title=titles.get(c.doc_id, ""),
                snippet=c.text[:200],
                score=round(c.score, 4),
            )
            for c in chunks
        ]
        resp = SuggestResp(text=text, sources=sources)
        if text:
            with _suggest_lock:
                _suggest_cache[cache_key] = (time.time(), resp.model_dump())
        return resp
    except Exception:  # noqa: BLE001 - fail-open：建议失败绝不打断客服工作
        logger.exception("agent assist suggest failed (session=%s)", session_id)
        return SuggestResp()
