"""Chat 路由（BU-06）：/api/v1/chat/stream SSE 流式问答。

事件协议（对齐 contracts/api.ts）：
    intent{intent,refuse} → stage{stage:retrieving} → stage{stage:generating}
    → token{delta}* → sources → done{message_id}
    异常任一点 → error{code,message}（fail-closed，不静默）

职责：校验 session 归属 → 配额预检 → 写 user 消息 → RAG 流式 → 落库 assistant 消息
+ message_sources 真源（知识来源唯一真源）→ 成功扣减配额。
MVP 单知识库策略：取当前租户最新一个 KB（多 KB 选择留 Phase2）。

R-6 断连语义（R2 已解决）：配额在 stream 开始前原子扣减（防刷）+ client_msg_id 幂等；
若中途断开 / 知识库为空 / 系统异常，refund 回滚已扣配额（不白扣）；重试同一 client_msg_id 不重复扣费。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.tracing import set_trace_id
from app.models.knowledge import Document
from app.models.message import Message, MessageRole, MessageSource
from app.models.session import Session
from app.services import conversation_state, quick_answers
from app.services.agent_metrics import drain_degraded
from app.services.agents.image_agent import ImageAgent
from app.services.agents.router import IMAGE_AGENT, TICKET_AGENT
from app.services.agents.router import router as agent_router
from app.services.agents.ticket_agent import TicketAgent
from app.services.answer_cache import put as cache_put

# 大扫查 O1：KB 定位/标题查询下沉服务层；别名导入保持既有测试 mock 目标
# （app.api.chat._latest_kb_id 重绑定本命名空间仍生效）。
from app.services.kb_lookup import doc_titles as _kb_doc_titles_sync
from app.services.kb_lookup import get_latest_kb_id as _latest_kb_id
from app.services.quick_answers import match_quick
from app.services.quota import get_quota_service
from app.services.rag_service import _split_tokens as _split_answer
from app.services.rag_service import stream_answer
from app.services.session_context import build_handoff_summary
from app.services.shared_context import SharedContext

# 保留 order_tool 命名空间绑定：既有测试 mock 目标 app.api.chat.order_tool.*
from app.services.tools import (
    TOOL_REGISTRY,
    order_tool,  # noqa: F401
)
from app.services.user_profile_service import (
    get_profile,
    merge_profile,
    to_prompt_text,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# v1.1 多 Agent 编排（方案书 §2）：Agent 均为无状态实例，模块级共享
ticket_agent = TicketAgent()
image_agent = ImageAgent()


class ChatStreamReq(BaseModel):
    session_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4000)
    stream: bool = True
    # R2：客户端提问幂等键（前端生成、重试复用）——配额幂等扣费，断连重试不重复扣
    client_msg_id: str | None = Field(default=None, max_length=64)
    # v1.3 图片理解：图片文件路径列表（前端上传后存储到临时目录，传递路径给后端）
    image_paths: list[str] = Field(default_factory=list)


#: SSE 事件名白名单（C2：与前端 contracts/api.ts SSEEvent union 对齐，防事件名漂移）
_SSE_EVENTS = frozenset({"stage", "intent", "token", "sources", "done", "error"})


def _sse(data: dict) -> str:
    ev = data.get("event")
    if ev not in _SSE_EVENTS:
        # 契约防御：非法事件名不 raise（会掐断 SSE 流），降级为 error 事件让前端感知
        logger.warning("SSE 事件名越界: %r（白名单: %s）", ev, sorted(_SSE_EVENTS))
        return _sse({"event": "error", "data": {"code": "SSE_CONTRACT", "message": f"未知 SSE 事件: {ev}"}})
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _kb_version_str(db: OrmSession, kb_id: uuid.UUID) -> str | None:
    """KB 版本指纹：就绪文档数 + 最新文档 created_at（T10 缓存失效锚点，文档增删均变化）。

    防御：SQLite（测试）下 Uuid 列可能读回 str，统一转 uuid 再入参。
    """
    kb_id = uuid.UUID(str(kb_id))
    from sqlalchemy import func

    cnt = (
        db.scalar(
            select(func.count(Document.id)).where(
                Document.kb_id == kb_id,
                Document.status == "indexed",  # DocumentStatus 就绪态（parsing/embedding/indexed/failed）
            )
        )
        or 0
    )
    latest = db.scalar(select(func.max(Document.created_at)).where(Document.kb_id == kb_id))
    return f"{cnt}:{latest.isoformat() if latest else ''}"


# ---- H2 修复：同步 DB / 阻塞调用统一搬出事件循环（run_in_threadpool） ----


async def _fetch_history(db: OrmSession, session_id: uuid.UUID, exclude_msg_id: uuid.UUID) -> list[dict]:
    """取最近 6 条历史（不含当前问题），在 worker 线程跑同步 DB 查询。"""

    def _work() -> list[dict]:
        rows = db.scalars(
            select(Message)
            .where(Message.session_id == session_id, Message.id != exclude_msg_id)
            .order_by(Message.created_at.desc())
            .limit(6)
        ).all()
        return [{"role": m.role.value, "content": m.content} for m in reversed(rows)]

    return await run_in_threadpool(_work)


async def _fetch_doc_titles(db: OrmSession, doc_ids: set[uuid.UUID]) -> dict[str, str]:
    """批量查文档标题（消息来源唯一真源），worker 线程跑同步查询。

    大扫查 O1：实现收敛 kb_lookup.doc_titles（三份同形拷贝 → 一份）。
    """
    return await run_in_threadpool(_kb_doc_titles_sync, db, doc_ids)


async def _persist_answer(
    db: OrmSession,
    session_id: uuid.UUID,
    content: str,
    source_payloads: list[dict],
    intent: str,
    meta: dict,
) -> str:
    """落库 assistant 消息 + 来源真源一次性提交（配额已在开始时原子扣减）。worker 线程跑同步 DB。

    R-2：intent 为真实判定（qa/handoff/chitchat），不再写死；
    R-3：meta 携带 first_token_ms 首字时延埋点（供 admin stats 聚合）。
    """

    def _work() -> str:
        msg = Message(
            session_id=session_id,
            role=MessageRole.assistant,
            content=content,
            intent=intent,
            meta=meta,
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        # BUG-03：touch 会话 updated_at（assistant 落库同样触发排序浮顶）
        sess = db.get(Session, session_id)
        if sess is not None:
            sess.updated_at = datetime.now(UTC)
            db.commit()
        for src in source_payloads:
            db.add(
                MessageSource(
                    message_id=msg.id,
                    chunk_id=uuid.UUID(src["chunk_id"]),
                    doc_id=uuid.UUID(src["doc_id"]),
                    doc_title=src.get("doc_title", ""),
                    snippet=src.get("snippet", "")[:500],
                    score=src.get("score", 0.0),
                )
            )
        db.commit()
        return str(msg.id)

    return await run_in_threadpool(_work)


def _update_conv_state_locked(db: OrmSession, session_id: uuid.UUID, message: str) -> dict:
    """P2-⑥：conv_state 读改写加行锁——重新 select 当前行（with_for_update）再合并写回，
    消除「请求进入时读取的旧 conv_state」被并发覆盖导致的丢更新。
    PG 方言下对行加锁、提交即释放；SQLite 测试自动降级（无锁语义，双双可跑）。
    """
    locked = db.scalar(select(Session).where(Session.id == session_id).with_for_update())
    if locked is None:
        raise RuntimeError(f"session not found: {session_id}")
    new_state = conversation_state.update(locked.conv_state, message)
    locked.conv_state = new_state
    db.commit()
    return new_state


@router.post("/stream")
async def chat_stream(
    req: ChatStreamReq,
    request: Request,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """SSE 流式问答（契约 ChatStreamReq / SSEEvent）。"""
    # R-7：契约要求 stream 恒为 true（SSE 语义），传 false 直接拒绝
    if not req.stream:
        raise HTTPException(status_code=422, detail="stream must be true")

    user_id = uuid.UUID(payload["sub"])
    try:
        session_id = uuid.UUID(req.session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid session_id")

    # 1) 校验会话归属：owner 可答；agent/admin 可代答（T5，身份写入消息 meta）；他人 user 拒绝（404 防探测）
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    role = payload.get("role")
    if s.user_id != user_id and role not in ("admin", "agent"):
        raise HTTPException(status_code=404, detail="session not found")
    is_agent_reply = s.user_id != user_id  # 代答：来源 agent/admin

    # 2) 配额原子扣减闸门（M2：try_consume 修复 TOCTOU，fail-closed 超额拒答）
    #    R2：client_msg_id 作幂等键 —— 断连重试同一请求不重复扣费
    #    P4：超额统一走 HTTP 429（不再 HTTP200+SSE error 双面不一致——客户端语义应看状态码）
    quota = get_quota_service()
    allowed, _ = quota.try_consume(str(user_id), 1, idem_key=req.client_msg_id)
    if not allowed:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "今日问答额度已用完")

    # 3) 写 user 消息（T5：代答时记录 agent 身份，溯源用）
    #    R2：落库失败 → 回滚已扣配额（消息没写成不扣费）
    try:
        user_msg = Message(
            session_id=session_id,
            role=MessageRole.user,
            content=req.content,
            intent="qa",
            meta={"agent_id": str(user_id)} if is_agent_reply else None,
        )
        db.add(user_msg)
        db.commit()
        db.refresh(user_msg)
        # BUG-03：touch 会话 updated_at（新消息后历史面板排序浮顶）
        s.updated_at = datetime.now(UTC)
        db.commit()
    except Exception:
        quota.refund(str(user_id), 1, req.client_msg_id)
        raise

    kb_id = await run_in_threadpool(_latest_kb_id, db)

    # T10：KB 版本（缓存失效锚点）= 就绪文档数:最新文档 created_at（文档增删均使版本变化）
    kb_version = None
    if kb_id is not None:
        kb_version = await run_in_threadpool(_kb_version_str, db, kb_id)

    # 画像归属：会话 owner。在 gen 外捕获：历史上 gen 内 sources 迭代用 `s` 遮蔽过外层
    # Session 导致闭包 UnboundLocalError（测试亲眼红过）；L2 改名 src 后遮蔽已消除，
    # gen 外捕获保留为防御惯例。
    session_owner_id = s.user_id
    # P0-1 trace_id：复用 HTTP 中间件生成的 request_id，贯通业务链路（日志 / SSE done）。
    trace_id = getattr(request.state, "request_id", "") or uuid.uuid4().hex[:12]

    async def gen():
        # P0-1：请求级 trace_id 注入 contextvar（RAG/Agent/工具日志统一带 trace_id；
        # asyncio 自动传播到同请求协程，anyio 线程池 worker 复制 context 同样生效）
        set_trace_id(trace_id)
        # S1（外部审查 2026-08-28）：配额已在上方原子扣减（R2 扣费在生成前），gen 一旦开始
        # 执行即「已扣费未交付」。consumed=True 表示配额仍被持有：未成功交付（done）的任何
        # 出口（error 事件/断连/异常）都由下方 finally 统一退款；done 分支在 assistant 落库
        # 成功后置 False（计费成立，不再退）。此前 error 事件只转发不退款——用户额度被静默侵蚀。
        consumed = True
        # BUG-09：客户端已断开 → 提前终止，停止后续 LLM 调用（避免浪费 token）
        if await request.is_disconnected():
            logger.info("chat stream aborted: client disconnected (pre)")
            quota.refund(str(user_id), 1, req.client_msg_id)  # R2：断连回滚配额，不白扣
            return
        if kb_id is None:
            quota.refund(str(user_id), 1, req.client_msg_id)  # R2：无知识库未生成 → 不扣费
            yield _sse({"event": "error", "data": {"code": "RAG_NO_KB", "message": "知识库为空，请先导入文档"}})
            return

        # 组装历史（最近 6 条，供 RAG 上下文；不含当前问题）
        history = await _fetch_history(db, session_id, user_msg.id)

        # 批次B：会话状态机——读旧状态 → 消息推进 → 写回 + 生成 prompt 提示（fail-open：
        # 任何异常降级为无状态，问答照常；conv_state=None 的旧会话按 new_state 处理）
        conv_state = None  # 批次C：try 前初始化——except 降级后仍需读 clarify_count
        try:
            # P2-⑥：行锁读改写（防止并发丢更新）——helper 内重读最新行并提交
            conv_state = await run_in_threadpool(_update_conv_state_locked, db, session_id, req.content)
            state_hint = conversation_state.to_prompt_hint(conv_state)
        except Exception:  # noqa: BLE001 - fail-open
            logger.exception("conv_state 更新失败（降级无状态，问答照常）")
            db.rollback()
            state_hint = None

        # 批次C：澄清额度 = MAX_CLARIFY - 已用次数（conv_state 为 None 视为 0 已用）
        clarify_left = max(
            0, conversation_state.MAX_CLARIFY - (conv_state or {}).get("clarify_count", 0)
        )

        # v1.1 Router 前置编排（方案书 §2.1）：前置意图分类（单一真源，规则式零 LLM）
        # + 决定执行计划（agents_invoked）。非阻塞关键词匹配，无需搬线程池。
        ctx = SharedContext(
            query=req.content,
            kb_id=kb_id,
            kb_version=kb_version,
            user_id=str(user_id),
            session_id=session_id,
            message_id=user_msg.id,
            history=history,
            db=db,  # 请求级会话：Ticket Agent 建单复用（同事务语义）
            image_paths=req.image_paths,  # v1.3 图片理解
            trace_id=trace_id,  # P0-1：请求级 trace_id 贯通 Router/各 Agent
            # 架构一期 4：移交摘要随 ctx 下发（Ticket Agent 建单时持久化进 tickets.summary）。
            # 取材 = history + 当前消息：_fetch_history 排除当前 user_msg，而触发移交的
            # 当前消息恰是坐席最需要的诉求（单轮移交 history 为空时摘要仍可打包）。
            # conv_state 用上方行锁读改写的最新值（fail-open 为 None，build 容错）。
            handoff_summary=build_handoff_summary(
                [*history, {"role": "user", "content": req.content}], conv_state
            ),
        )
        ctx = agent_router.route(ctx)

        # v1.3 图片理解：如果 Router 决定调用 Image Agent 且有图片，则执行图片理解
        if IMAGE_AGENT in ctx.agents_invoked and ctx.image_paths:
            ctx = await image_agent.run(ctx)

        # 批次D：订单工具分支——槽位有订单号 + 订单类主题 → 查单模板回答（零 LLM，
        # 事实型查询不冒幻觉）；查不到/异常回落 RAG（fail-open，不阻断）
        order_info = None
        # P0-2：经注册表取工具描述（元数据 + 执行函数），不再直接依赖具体模块
        order_tool_desc = TOOL_REGISTRY.get("order_query")
        _order_slot = (conv_state or {}).get("slots", {}).get(conversation_state.SLOT_ORDER_NO)
        if _order_slot and order_tool_desc and (conv_state or {}).get("topic") in order_tool_desc.topics:
            try:
                order_info = await run_in_threadpool(order_tool_desc.executor, _order_slot)
            except Exception:  # noqa: BLE001 - 工具异常回落 RAG
                logger.exception("订单工具查询失败（回落 RAG）")
                order_info = None

        assistant_parts: list[str] = []
        source_payloads: list[dict] = []
        intent = "qa"  # R-2：默认 qa，收到 intent 事件后用真实判定
        ticket_id: str | None = None  # T1：handoff 建单结果（fail-open，None=未建/失败）
        first_token_ms: float | None = None  # R-3：首字时延埋点（毫秒）
        cache_hit = False  # T10：缓存命中标记（落库 meta + 跳过回填）
        t0 = time.monotonic()
        try:
            # BUG-09：RAG/LLM 生成前再确认连接（检索可能耗时数秒）
            if await request.is_disconnected():
                logger.info("chat stream aborted: client disconnected (pre-llm)")
                return  # S1：consumed 仍 True → finally 统一退款（R2 断连回滚）
            quick_ans = await run_in_threadpool(match_quick, req.content)

            # 2026-08-22 Phase C：读取用户画像注入 prompt（fail-open：读取异常 → 不注入，回答照常）。
            # 画像归属会话 owner（session_owner_id）；仅影响 prompt，不影响缓存 key。
            user_profile: str | None = None
            try:
                if settings.USER_PROFILE_ENABLED:
                    p = await run_in_threadpool(get_profile, db, session_owner_id)
                    user_profile = to_prompt_text(p)
            except Exception:  # noqa: BLE001 - fail-open
                logger.exception("读取用户画像失败（不注入，回答照常）")

            async def _events():
                # 方案A：快捷预置话术短路——命中固定问句（按钮或手打同文案）直接秒回，不检索/不判缓存版本。
                # 诚实标注（2026-08-25 溯源空面板排查）：该分支不检索、零 LLM，
                # 不发 retrieving/generating 进度剧场（此前谎报「已检索知识库」）；
                # done 带 answer_source=quick，前端 SourcePanel 区分「预置话术无引用」空态。
                # 5-2 失效面：KB 变更后新版本未通过覆盖检查（is_enabled_for）→ 不短路自然落 RAG，
                # 防陈旧话术；从未跑过覆盖检查的环境恒放行（向后兼容），禁用时按版本 warning 一次。
                if quick_ans and quick_answers.is_enabled_for(kb_version):
                    yield ("intent", {"intent": "qa", "refuse": False})
                    for delta in _split_answer(quick_ans):
                        yield ("token", {"delta": delta})
                    yield ("sources", {"sources": []})
                    yield ("done", {"message_id": "", "answer_source": "quick"})
                    return
                # 批次D：订单工具短路——零 LLM 模板回答（quick_ans 同构事件形态）
                if order_info is not None:
                    reply_text = order_tool.format_order_reply(order_info)
                    yield ("intent", {"intent": "qa", "refuse": False})
                    yield ("stage", {"stage": "retrieving", "msg": "已查询订单"})
                    yield ("stage", {"stage": "generating", "msg": "正在生成回答"})
                    # 整段单 token 下发：_split_answer 8 字分片会把订单号/物流单号截断
                    # 跨事件（SSE 逐 delta JSON 编码，前端无法重组为可复制整号）
                    yield ("token", {"delta": reply_text})
                    yield ("sources", {"sources": []})
                    # message_id 为内部占位（勿消费）：外层循环恒用 _persist_answer 的真 id
                    # 重建 done_data 再下发客户端，此空串永不出网——防未来直接转发 data 泄漏假 id
                    yield ("done", {"message_id": "", "tool": "order_query"})
                    return
                # v1.3 图片理解：如果有融合查询（Image Agent 成功处理），使用融合查询
                search_query = ctx.fused_query if ctx.fused_query else req.content
                async for e, d in stream_answer(
                    search_query,
                    kb_id,
                    history=history,
                    kb_version=kb_version,
                    user_profile=user_profile,
                    state_hint=state_hint,
                    clarify_left=clarify_left,
                ):
                    yield (e, d)

            async for event, data in _events():
                # BUG-09：每收到一个事件检查客户端连接，断开即终止（不再消费下一个事件）
                if await request.is_disconnected():
                    logger.info("chat stream aborted: client disconnected during %s", event)
                    return  # S1：consumed 仍 True → finally 统一退款（R2 断连回滚）
                if event == "intent":
                    # R-2：真实意图（qa/handoff/chitchat）——落库用 + 转发客户端
                    intent = data.get("intent", "qa")
                    # H2（外部审查 2026-08-22）：拒答是 intent 事件里的布尔标志（{"intent":"qa","refuse":true}），
                    # 落库时折叠为专属值 refuse——否则 admin 待补录 Top10（intent=='refuse'）数据源恒空
                    if data.get("refuse") and intent == "qa":
                        intent = "refuse"
                    # Bug 修复：回写 user 消息真实 intent（此前恒 qa 导致
                    # admin hot_gaps（聚合 refuse 用户消息）数据源失效）
                    if intent != user_msg.intent:
                        user_msg.intent = intent
                        await run_in_threadpool(db.commit)
                    yield _sse({"event": "intent", "data": data})
                    # T1 + v1.1：handoff → Ticket Agent 建单（幂等 + fail-open，独立于流，不阻断）。
                    # Router 已前置判定（与管线内 intent 节点同源，必然一致）；
                    # 兜底：若 ctx.ticket_id 为空（Router 未排入或建单失败降级），补建一次，
                    # 与既有行为对齐——任何情况下 handoff 都保证尝试过建单。
                    # P4：统一 async 契约——await agent.run（内部阻塞经线程池），
                    # 不再按 Agent 区分 run_in_threadpool/await 两种形态。
                    if intent == "handoff":
                        if ctx.ticket_id is None and TICKET_AGENT in ctx.agents_invoked:
                            ctx = await ticket_agent.run(ctx)
                        if ctx.ticket_id is None:
                            logger.warning("handoff 建单降级（degraded=%s）", ctx.degraded)
                        ticket_id = ctx.ticket_id
                elif event == "stage":
                    yield _sse({"event": "stage", "data": data})
                elif event == "token":
                    if first_token_ms is None:
                        first_token_ms = round((time.monotonic() - t0) * 1000, 1)
                    assistant_parts.append(data["delta"])
                    yield _sse({"event": "token", "data": data})
                elif event == "sources":
                    # 补文档标题（检索结果只有 doc_id，标题需查 DB；消息来源唯一真源）。
                    # L2（外部审查 2026-08-29 核实）：迭代变量用 src，不再遮蔽外层 Session `s`。
                    doc_ids = {uuid.UUID(src["doc_id"]) for src in data["sources"] if src.get("doc_id")}
                    doc_titles = await _fetch_doc_titles(db, doc_ids)
                    for src in data["sources"]:
                        src["doc_title"] = doc_titles.get(src.get("doc_id", ""), "")
                    source_payloads = data["sources"]
                    yield _sse({"event": "sources", "data": data})
                elif event == "done":
                    # 批次C：澄清轮 → 会话状态置 clarifying + 计数+1（fail-open，写库异常不影响响应）
                    # 大扫查修复：转移逻辑收归 conversation_state.mark_clarifying（单一真源）
                    if data.get("clarify"):
                        try:
                            s.conv_state = conversation_state.mark_clarifying(s.conv_state)
                            await run_in_threadpool(db.commit)
                        except Exception:  # noqa: BLE001 - fail-open
                            logger.exception("clarify 状态写回失败（不影响响应）")
                            db.rollback()
                    # 落库 assistant 消息 + 来源真源 + 真实 intent + 首字时延埋点
                    content = "".join(assistant_parts)
                    meta = {"first_token_ms": first_token_ms} if first_token_ms is not None else {}
                    if data.get("tool"):
                        meta["tool"] = data["tool"]  # 批次D：工具回答可观测
                    if data.get("clarify"):
                        meta["clarify"] = True  # T1.1：澄清轮落库可辨（运营区分澄清与真拒答）
                    if data.get("cache_hit"):
                        cache_hit = True
                        meta["cache_hit"] = True  # T10：缓存命中可溯源
                    if data.get("answer_source"):
                        meta["answer_source"] = data["answer_source"]  # 快捷话术可辨（SourcePanel 区分空态）
                    msg_id = await _persist_answer(
                        db, session_id, content, source_payloads, intent, meta
                    )
                    # S1：assistant 已落库 = 交付成立，计费生效（finally 不再退）。
                    # 置于落库成功之后——落库失败仍走 finally 退款（不白扣）。
                    consumed = False
                    # 2026-08-22 Phase B：assistant 落库后增量采集用户画像（幂等键=user_msg.id；
                    # fail-open：采集异常不影响响应；手打/快捷问题都记主题与实体）。
                    # 归属用 session_owner_id（会话 owner）而非当前操作者：agent/admin 代答时不把画像记到客服头上。
                    try:
                        await run_in_threadpool(
                            merge_profile,
                            db,
                            session_owner_id,
                            req.content,
                            intent=intent,
                            idem_key=str(user_msg.id),
                        )
                    except Exception:  # noqa: BLE001 - 采集兜底（merge_profile 内部已 fail-open，双保险）
                        logger.exception("用户画像采集异常（不影响响应）")
                    # T10：未命中 → 回填缓存（qa 非拒答且有内容；复用 RAG 已生成的改写 key）。
                    # 避免在 done 路径重复调用 rewrite，保证检索与回填的 key 同源。
                    rewritten_query = data.get("rewritten_query")
                    if (
                        not cache_hit
                        and intent == "qa"
                        and state_hint is None  # P2-③：会话状态影响回答 → 不进全局缓存
                        and not user_profile  # P2-③：个性化用户不进精确层缓存（正确性优先）
                        and content
                        and isinstance(rewritten_query, str)
                        and rewritten_query
                    ):
                        try:
                            await run_in_threadpool(
                                cache_put,
                                rewritten_query,
                                content,
                                source_payloads,
                                [s.get("doc_id", "") for s in source_payloads],
                                kb_version,
                                str(kb_id) if kb_id else None,
                            )
                        except Exception:  # noqa: BLE001 - fail-open
                            logger.exception("缓存回填失败（不影响响应）")
                    # T1：done 携带 ticket_id（handoff 场景前端展示工单号）
                    # T10 修复：转发 cache_hit（stream_answer 命中时前端可感知缓存答案）
                    # R2/C4：done 回传 user_message_id（本次提问后端真 id）→ 前端本地消息 id 对齐后端
                    done_data: dict = {
                        "message_id": msg_id,
                        "ticket_id": ticket_id,
                        "user_message_id": str(user_msg.id),
                        "trace_id": trace_id,  # P0-1：可选字段透出，前端排查可追同一条链路
                    }
                    if cache_hit:
                        done_data["cache_hit"] = True
                    if data.get("clarify"):
                        done_data["clarify"] = True  # 批次C：澄清轮前端可感知（引导补充信息）
                    if data.get("tool"):
                        done_data["tool"] = data["tool"]  # 大扫查O2：工具回答透传（与 meta 同源）
                    if data.get("answer_source"):
                        done_data["answer_source"] = data["answer_source"]  # 快捷话术透传（前端空态区分）
                    yield _sse({"event": "done", "data": done_data})
                elif event == "error":
                    # S1：error = 已扣费但未交付（rag_service 两个 error 源都在扣费后），
                    # consumed 保持 True → finally 统一退款（此前只转发不退款，额度被静默侵蚀）。
                    yield _sse({"event": "error", "data": data})
        except Exception:  # pragma: no cover - 兜底，不向客户端泄漏内部细节
            logger.exception("chat stream 处理异常")
            yield _sse({"event": "error", "data": {"code": "SYS_ERROR", "message": "服务异常，请稍后重试"}})
        finally:
            # S1：未交付出口统一退款（error 事件/断连/异常），单一收口防泄漏也防双退；
            # done 已置 consumed=False（交付成立不退）。quota.refund 内部幂等标记再兜底一层。
            if consumed:
                quota.refund(str(user_id), 1, req.client_msg_id)
                consumed = False
            # P2-2：Agent 降级排水——正常结束/断连/异常全路径统一计数 + 结构化日志
            # （防静默改路径：降级率从此可统计、可告警）
            drain_degraded(ctx.degraded, trace_id=trace_id)

    return StreamingResponse(gen(), media_type="text/event-stream")
