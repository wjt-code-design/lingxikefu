"""RAG 管线（BU-03/BU-05 核心）：intent → 检索 → prompt → 流式生成。

设计（vet-plan 裁定）：
- 纯函数式 4 步，不做 LangGraph / query rewrite / sparse / rerank（MVP 关闭，
  接口预留开关，recall 基线不达标再开）。
- intent 用规则式（轻量、省 LLM 调用）：闲聊/转人工关键词命中即短路，
  不再调 LLM 做意图分类（单租户客服场景关键词足够）。
- 诚实性：检索 top-1 分数低于阈值 → 拒答提示转人工，绝不编造（fail-closed）。
- 所有 RAG 阶段错误抛 RagError，由 Chat 层转 SSE error 事件。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from app.core.config import settings
from app.llm_clients.chat import get_chat_client
from app.prompts.qa_prompt import build_qa_messages
from app.services.retrieval_service import RetrievalError, RetrievedChunk, search_kb

logger = logging.getLogger(__name__)


class RagError(Exception):
    """RAG 管线失败（Chat 层应转 SSE error，不静默）。"""


#: 闲聊 / 转人工关键词（规则式 intent，命中即短路，不检索不调 LLM）
HANDOFF_KEYWORDS = (
    "人工", "客服", "投诉", "转人工", "经理", "真人", "电话", "催",
)
CHITCHAT_KEYWORDS = (
    "你好", "在吗", "谢谢", "再见", "你是谁", "你是机器人", "真人",
    "天气", "笑话", "几点下班", "下班", "心情",
)

#: 检索分数低于此值视为无可靠依据 → 拒答（bge cosine 经验阈值，M3 评测校准）
MIN_SCORE = 0.30


@dataclass
class RagResult:
    """管线产物：Chat 层据此发 SSE 事件。"""

    intent: str  # qa | handoff | chitchat
    chunks: list[RetrievedChunk] = field(default_factory=list)
    refuse: bool = False  # 无依据拒答（诚实性）
    refuse_reason: str = ""


def classify_intent(query: str) -> str:
    """规则式意图分类：handoff > chitchat > qa。"""
    if any(k in query for k in HANDOFF_KEYWORDS):
        return "handoff"
    if any(k in query for k in CHITCHAT_KEYWORDS):
        return "chitchat"
    return "qa"


def run_pipeline(query: str, kb_id: UUID, top_k: int = 8) -> RagResult:
    """RAG 管线入口（非流式部分）：intent → 检索 → 拒答判定。

    返回 RagResult，生成阶段由 Chat 层用 build_qa_messages 组装后流式调用。
    """
    intent = classify_intent(query)
    result = RagResult(intent=intent)

    if intent == "qa":
        try:
            chunks = search_kb(query, kb_id, top_k=top_k)
        except RetrievalError as e:
            raise RagError(f"检索不可用: {e}") from e
        result.chunks = chunks
        # 诚实性：无依据拒答（top-1 分数过低 = 检索结果不可信，绝不编造）
        if not chunks or chunks[0].score < MIN_SCORE:
            result.refuse = True
            result.refuse_reason = "未找到可靠依据"
        logger.info("RAG qa: top1_score=%.3f refuse=%s", chunks[0].score if chunks else None, result.refuse)

    return result


async def stream_answer(
    query: str,
    kb_id: UUID,
    history: list[dict] | None = None,
    top_k: int = 8,
):
    """流式回答：yield (event_type, data)。

    event_type: stage | token | sources | done | error
    - stage/retrieving → stage/generating → sources → token* → done
    - 拒答/闲聊/转人工 不发 token，直接 sources([])+done
    - 任一异常 → error（fail-closed，不静默）
    """
    result = RagResult(intent="qa")
    try:
        result = run_pipeline(query, kb_id, top_k=top_k)
    except RagError as e:
        yield ("error", {"code": "RAG_RETRIEVAL", "message": str(e)})
        return

    yield ("stage", {"stage": "retrieving", "msg": "已检索知识库"})
    yield ("stage", {"stage": "generating", "msg": "正在生成回答"})
    if result.intent != "qa" or result.refuse:
        msg = _no_llm_reply(result)
        yield ("sources", {"sources": []})
        for delta in _split_tokens(msg):
            yield ("token", {"delta": delta})
        yield ("done", {"message_id": ""})
        return

    try:
        messages = build_qa_messages(
            query=query,
            chunks=result.chunks,
            history=history or [],
        )
        client = get_chat_client()
        async for delta in client.stream(messages, model=settings.CHAT_MODEL):
            yield ("token", {"delta": delta})
        yield ("sources", {"sources": _to_sources(result.chunks)})
        yield ("done", {"message_id": ""})
    except Exception as e:  # noqa: BLE001
        logger.exception("RAG 生成失败")
        yield ("error", {"code": "RAG_GENERATE", "message": f"生成失败: {e}"})


def _no_llm_reply(result: RagResult) -> str:
    if result.intent == "handoff":
        return "已为您转接人工客服，请稍候。您也可以描述具体问题，我会先帮您查询。"
    if result.intent == "chitchat":
        return "我是星河智家智能客服，可以帮您解答退换货、保修、配送等问题。有什么可以帮您？"
    return "抱歉，我暂时没有找到关于这个问题的可靠信息，为避免误导您，建议转人工客服处理。"


def _to_sources(chunks: list[RetrievedChunk]) -> list[dict]:
    return [
        {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "score": round(c.score, 4),
            "text": c.text[:200],
        }
        for c in chunks
    ]


def _split_tokens(text: str, size: int = 8) -> list[str]:
    """非流式回复切成小片模拟流式（前端 SSE 展示流畅）。"""
    return [text[i : i + size] for i in range(0, len(text), size)]
