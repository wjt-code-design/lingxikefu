"""Chat 路由（BU-06）：/api/v1/chat/stream SSE 流式问答。

事件协议（对齐 contracts/api.ts）：
    intent{intent,refuse} → stage{stage:retrieving} → stage{stage:generating}
    → token{delta}* → sources → done{message_id}
    异常任一点 → error{code,message}（fail-closed，不静默）

职责：校验 session 归属 → 配额预检 → 写 user 消息 → RAG 流式 → 落库 assistant 消息
+ message_sources 真源（知识来源唯一真源）→ 成功扣减配额。
MVP 单知识库策略：取当前租户最新一个 KB（多 KB 选择留 Phase2）。

R-6 断连语义（已知约束）：配额在 stream 开始前原子扣减（防刷）、user 消息在
判定前落库。若客户端中途断开，assistant 消息不落库 → 配额白扣 + 孤儿 user 消息；
重试会再次扣减。MVP 阶段接受（防刷优先），Phase2 引入断连续传时再改。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge import Document, KnowledgeBase
from app.models.message import Message, MessageRole, MessageSource
from app.models.session import Session
from app.api.tickets import ensure_active_ticket
from app.services.answer_cache import put as cache_put
from app.services.query_rewrite import rewrite
from app.services.quota import get_quota_service
from app.services.rag_service import stream_answer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatStreamReq(BaseModel):
    session_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4000)
    stream: bool = True


class AgentReplyReq(BaseModel):
    """T5：agent/admin 直复（不经 AI），写入用户会话 assistant 消息。"""
    session_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4000)


#: SSE 事件名白名单（C2：与前端 contracts/api.ts SSEEvent union 对齐，防事件名漂移）
_SSE_EVENTS = frozenset({"stage", "intent", "token", "sources", "done", "error"})


def _sse(data: dict) -> str:
    ev = data.get("event")
    if ev not in _SSE_EVENTS:
        # 契约防御：非法事件名不 raise（会掐断 SSE 流），降级为 error 事件让前端感知
        logger.warning("SSE 事件名越界: %r（白名单: %s）", ev, sorted(_SSE_EVENTS))
        return _sse({"event": "error", "data": {"code": "SSE_CONTRACT", "message": f"未知 SSE 事件: {ev}"}})
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


#: 最新 KB id 缓存（L7 + R-5）：单租户 KB 极少变动，60s TTL 内复用避免每请求查 DB。
#: R-5 修正：不缓存 None（新建 KB 立即可感知）+ 线程锁保护（run_in_threadpool 并发读写）。
_kb_cache: tuple[float, uuid.UUID] | None = None
_kb_lock = threading.Lock()
_KB_CACHE_TTL = 60.0


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


def _latest_kb_id(db: OrmSession) -> uuid.UUID | None:
    """MVP 单知识库：当前租户最新创建的 KB（带短 TTL 缓存，L7/R-5）。"""
    global _kb_cache
    with _kb_lock:
        now = time.time()
        if _kb_cache and now - _kb_cache[0] < _KB_CACHE_TTL:
            return _kb_cache[1]
        kb = db.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == settings.TENANT_DEFAULT)
            .order_by(KnowledgeBase.created_at.desc())
            .limit(1)
        )
        kb_id = kb.id if kb else None
        # SQLite（测试）下 Uuid 列可能读回 str，统一转 uuid 缓存（生产 PG 本就是 uuid）
        if kb_id is not None and not isinstance(kb_id, uuid.UUID):
            kb_id = uuid.UUID(str(kb_id))
        # 仅在确有 KB 时缓存；无 KB 不缓存 → 新建后立即生效
        _kb_cache = (now, kb_id) if kb_id is not None else None
        return kb_id


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
    """批量查文档标题（消息来源唯一真源），worker 线程跑同步查询。"""
    if not doc_ids:
        return {}

    def _work() -> dict[str, str]:
        return {
            str(d.id): d.name
            for d in db.scalars(select(Document).where(Document.id.in_(doc_ids))).all()
        }

    return await run_in_threadpool(_work)


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


@router.post("/reply")
def agent_reply(
    req: AgentReplyReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
) -> dict:
    """T5：agent/admin 直复文本（不经 AI），写入用户会话 assistant 消息并记录 agent 身份。

    用于人工接管后的直接回复；用户仍用 /chat/stream（AI 问答）。
    """
    role = payload.get("role")
    if role not in ("admin", "agent"):
        raise HTTPException(status_code=403, detail="agent/admin role required")
    try:
        session_id = uuid.UUID(req.session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid session_id")
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    msg = Message(
        session_id=session_id,
        role=MessageRole.assistant,
        content=req.content,
        intent="agent_reply",
        meta={"agent_id": str(uuid.UUID(payload["sub"]))},
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"message_id": str(msg.id)}


@router.post("/stream")
async def chat_stream(
    req: ChatStreamReq,
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
    quota = get_quota_service()
    allowed, _ = quota.try_consume(str(user_id), 1)
    if not allowed:
        return StreamingResponse(
            _sse({"event": "error", "data": {"code": "QUOTA_EXCEEDED", "message": "今日问答额度已用完"}}),
            media_type="text/event-stream",
        )

    # 3) 写 user 消息（T5：代答时记录 agent 身份，溯源用）
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

    kb_id = await run_in_threadpool(_latest_kb_id, db)

    # T10：KB 版本（缓存失效锚点）= 就绪文档数:最新文档 created_at（文档增删均使版本变化）
    kb_version = None
    if kb_id is not None:
        kb_version = await run_in_threadpool(_kb_version_str, db, kb_id)

    async def gen():
        if kb_id is None:
            yield _sse({"event": "error", "data": {"code": "RAG_NO_KB", "message": "知识库为空，请先导入文档"}})
            return

        # 组装历史（最近 6 条，供 RAG 上下文；不含当前问题）
        history = await _fetch_history(db, session_id, user_msg.id)

        assistant_parts: list[str] = []
        source_payloads: list[dict] = []
        intent = "qa"  # R-2：默认 qa，收到 intent 事件后用真实判定
        ticket_id: str | None = None  # T1：handoff 建单结果（fail-open，None=未建/失败）
        first_token_ms: float | None = None  # R-3：首字时延埋点（毫秒）
        cache_hit = False  # T10：缓存命中标记（落库 meta + 跳过回填）
        t0 = time.monotonic()
        try:
            async for event, data in stream_answer(
                req.content, kb_id, history=history, kb_version=kb_version
            ):
                if event == "intent":
                    # R-2：真实意图（qa/handoff/chitchat）——落库用 + 转发客户端
                    intent = data.get("intent", "qa")
                    # Bug 修复：回写 user 消息真实 intent（此前恒 qa 导致
                    # admin hot_gaps（聚合 handoff/refuse 用户消息）数据源失效）
                    if intent != user_msg.intent:
                        user_msg.intent = intent
                        await run_in_threadpool(db.commit)
                    yield _sse({"event": "intent", "data": data})
                    # T1：handoff → AI 建单（幂等 + fail-open，独立于流，不阻断）
                    if intent == "handoff":
                        ticket = await run_in_threadpool(
                            ensure_active_ticket, db, session_id, user_msg.id
                        )
                        if ticket is not None:
                            ticket_id = str(ticket.id)
                elif event == "stage":
                    yield _sse({"event": "stage", "data": data})
                elif event == "token":
                    if first_token_ms is None:
                        first_token_ms = round((time.monotonic() - t0) * 1000, 1)
                    assistant_parts.append(data["delta"])
                    yield _sse({"event": "token", "data": data})
                elif event == "sources":
                    # 补文档标题（检索结果只有 doc_id，标题需查 DB；消息来源唯一真源）
                    doc_ids = {uuid.UUID(s["doc_id"]) for s in data["sources"] if s.get("doc_id")}
                    doc_titles = await _fetch_doc_titles(db, doc_ids)
                    for s in data["sources"]:
                        s["doc_title"] = doc_titles.get(s.get("doc_id", ""), "")
                    source_payloads = data["sources"]
                    yield _sse({"event": "sources", "data": data})
                elif event == "done":
                    # 落库 assistant 消息 + 来源真源 + 真实 intent + 首字时延埋点
                    content = "".join(assistant_parts)
                    meta = {"first_token_ms": first_token_ms} if first_token_ms is not None else {}
                    if data.get("cache_hit"):
                        cache_hit = True
                        meta["cache_hit"] = True  # T10：缓存命中可溯源
                    msg_id = await _persist_answer(
                        db, session_id, content, source_payloads, intent, meta
                    )
                    # T10：未命中 → 回填缓存（qa 非拒答且有内容；改写后 query 作 key）
                    if not cache_hit and intent == "qa" and content:
                        rewritten, _r = rewrite(req.content, history)
                        if rewritten and content:
                            try:
                                await run_in_threadpool(
                                    cache_put,
                                    rewritten,
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
                    done_data: dict = {"message_id": msg_id, "ticket_id": ticket_id}
                    if cache_hit:
                        done_data["cache_hit"] = True
                    yield _sse({"event": "done", "data": done_data})
                elif event == "error":
                    yield _sse({"event": "error", "data": data})
        except Exception:  # pragma: no cover - 兜底，不向客户端泄漏内部细节
            logger.exception("chat stream 处理异常")
            yield _sse({"event": "error", "data": {"code": "SYS_ERROR", "message": "服务异常，请稍后重试"}})

    return StreamingResponse(gen(), media_type="text/event-stream")
