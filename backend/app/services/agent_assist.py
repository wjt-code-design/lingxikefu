"""坐席辅助核心服务（架构二期 1）：suggest 端点与建单 AI 预起草共用的草拟流程。

抽取动机（L2 预起草）：低风险 handoff 建单后需在后台用「KB 定位 → 检索 top3 →
会话历史 → assist prompt → LLM 非流式」为坐席预草拟回复，与 sessions.suggest_reply
（批次A）核心完全同构——收敛为单一 ``draft_reply()``，两处共用防逻辑漂移。

HTTP 端点行为保持（现测试 test_sessions_suggest 锁定）：
- 端点把自身命名空间绑定的依赖（``_latest_kb_id`` / ``search_kb`` / ``get_chat_client``
  ——既有测试的 mock 目标）按关键字参数注入，调用时序/缓存/降级路径逐字不变；
- 后台预起草（ticket_service.draft_ticket_suggestion）不传注入参数，走模块默认依赖
  （``_default_*``，亦为 worker 测试的 mock 目标）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.llm_clients.chat import get_chat_client as _default_chat_client
from app.models.message import Message
from app.prompts.agent_assist_prompt import build_assist_messages
from app.services.kb_lookup import get_latest_kb_id as _default_kb_lookup
from app.services.retrieval_service import RetrievedChunk
from app.services.retrieval_service import search_kb as _default_search_kb

#: P2-⑤：坐席辅助 LLM 短超时（25s < 前端 35s 请求阈值；跨端契约由
#: test_sessions_suggest::test_suggest_frontend_timeout_above_backend_max 锁定）
ASSIST_TIMEOUT = 25
#: 检索条数（与 suggest 端点一致：top3）
ASSIST_TOP_K = 3


@dataclass
class AssistDraft:
    """草拟产物：text 空 = 失败/无知识库（fail-open）；chunks 供端点补标题成 sources。"""

    text: str = ""
    chunks: list[RetrievedChunk] = field(default_factory=list)


def fetch_history(db: OrmSession, session_id: uuid.UUID, limit: int = 6) -> list[dict]:
    """最近 N 条会话消息（任意角色，时间正序）——suggest 端点原 _history 同构。"""
    rows = (
        db.scalars(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        .all()
    )
    return [{"role": m.role.value, "content": m.content} for m in reversed(rows)]


async def draft_reply(
    db: OrmSession,
    session_id: uuid.UUID,
    question: str,
    state_hint: str | None = None,
    *,
    latest_kb_id=None,
    search_kb=None,
    chat_client=None,
) -> AssistDraft:
    """草拟一条坐席回复（单一核心，检索 + assist prompt + LLM 非流式）。

    - 同步阻塞段（KB 定位/检索/历史查询）经 run_in_threadpool 执行（H2 纪律，
      不阻塞事件循环——后台 worker 经 asyncio.run 调用同样成立）；
    - 注入参数缺省用模块默认实现；任何一步异常向调用方传播（端点 fail-open 返回
      空建议；worker fail-open 草稿留空）。
    """
    kb_lookup = latest_kb_id or _default_kb_lookup
    search_fn = search_kb or _default_search_kb
    client_factory = chat_client or _default_chat_client

    kb_id = await run_in_threadpool(kb_lookup, db)
    if kb_id is None:
        return AssistDraft()  # 无知识库：空草稿（fail-open）
    chunks = await run_in_threadpool(search_fn, question, kb_id, ASSIST_TOP_K)
    history = await run_in_threadpool(fetch_history, db, session_id)
    messages = build_assist_messages(
        question=question, history=history, chunks=chunks, state_hint=state_hint
    )
    text = (await client_factory().complete(messages, timeout=ASSIST_TIMEOUT)).strip()
    return AssistDraft(text=text, chunks=chunks)
