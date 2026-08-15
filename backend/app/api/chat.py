"""Chat 路由（BU-06）：/api/v1/chat/stream SSE 流式问答。

事件协议（对齐 contracts/api.ts）：
    stage{stage:retrieving} → stage{stage:generating} → token{delta}* → sources → done{message_id}
    异常任一点 → error{code,message}（fail-closed，不静默）

职责：校验 session 归属 → 配额预检 → 写 user 消息 → RAG 流式 → 落库 assistant 消息
+ message_sources 真源（知识来源唯一真源）→ 成功扣减配额。
MVP 单知识库策略：取当前租户最新一个 KB（多 KB 选择留 Phase2）。
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
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
from app.services.quota import get_quota_service
from app.services.rag_service import stream_answer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatStreamReq(BaseModel):
    session_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4000)
    stream: bool = True


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _latest_kb_id(db: OrmSession) -> uuid.UUID | None:
    """MVP 单知识库：当前租户最新创建的 KB。"""
    kb = db.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.tenant_id == settings.TENANT_DEFAULT)
        .order_by(KnowledgeBase.created_at.desc())
        .limit(1)
    )
    return kb.id if kb else None


@router.post("/stream")
async def chat_stream(
    req: ChatStreamReq,
    payload: dict = Depends(get_current_user),
    db: OrmSession = Depends(get_db),
):
    """SSE 流式问答（契约 ChatStreamReq / SSEEvent）。"""
    user_id = uuid.UUID(payload["sub"])
    try:
        session_id = uuid.UUID(req.session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid session_id")

    # 1) 校验会话归属（防越权访问他人会话）
    s = db.scalar(select(Session).where(Session.id == session_id))
    if not s or s.user_id != user_id:
        raise HTTPException(status_code=404, detail="session not found")

    # 2) 配额预检（fail-closed：超额直接拒答，不调 LLM）
    quota = get_quota_service()
    if quota.left_today(str(user_id)) <= 0:
        return StreamingResponse(
            _sse({"event": "error", "data": {"code": "QUOTA_EXCEEDED", "message": "今日问答额度已用完"}}),
            media_type="text/event-stream",
        )

    # 3) 写 user 消息
    user_msg = Message(session_id=session_id, role=MessageRole.user, content=req.content, intent="qa")
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    kb_id = _latest_kb_id(db)

    async def gen():
        if kb_id is None:
            yield _sse({"event": "error", "data": {"code": "RAG_NO_KB", "message": "知识库为空，请先导入文档"}})
            return

        # 组装历史（最近 6 条，供 RAG 上下文；不含当前问题）
        rows = db.scalars(
            select(Message)
            .where(Message.session_id == session_id, Message.id != user_msg.id)
            .order_by(Message.created_at.desc())
            .limit(6)
        ).all()
        history = [
            {"role": m.role.value, "content": m.content} for m in reversed(rows)
        ]

        assistant_parts: list[str] = []
        source_payloads: list[dict] = []
        try:
            async for event, data in stream_answer(req.content, kb_id, history=history):
                if event == "stage":
                    yield _sse({"event": "stage", "data": data})
                elif event == "token":
                    assistant_parts.append(data["delta"])
                    yield _sse({"event": "token", "data": data})
                elif event == "sources":
                    # 补文档标题（检索结果只有 doc_id，标题需查 DB；消息来源唯一真源）
                    doc_ids = {uuid.UUID(s["doc_id"]) for s in data["sources"] if s.get("doc_id")}
                    doc_titles = {}
                    if doc_ids:
                        for d in db.scalars(select(Document).where(Document.id.in_(doc_ids))).all():
                            doc_titles[str(d.id)] = d.name
                    for s in data["sources"]:
                        s["doc_title"] = doc_titles.get(s.get("doc_id", ""), "")
                    source_payloads = data["sources"]
                    yield _sse({"event": "sources", "data": data})
                elif event == "done":
                    # 落库 assistant 消息 + 来源真源 + 配额扣减
                    content = "".join(assistant_parts)
                    msg = Message(session_id=session_id, role=MessageRole.assistant, content=content, intent="qa")
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
                    quota.increment(str(user_id), 1)  # 成功生成才扣减
                    yield _sse({"event": "done", "data": {"message_id": str(msg.id)}})
                elif event == "error":
                    yield _sse({"event": "error", "data": data})
        except Exception:  # pragma: no cover - 兜底，不向客户端泄漏内部细节
            logger.exception("chat stream 处理异常")
            yield _sse({"event": "error", "data": {"code": "SYS_ERROR", "message": "服务异常，请稍后重试"}})

    return StreamingResponse(gen(), media_type="text/event-stream")
