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
import re
from dataclasses import dataclass, field
from uuid import UUID

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.llm_clients.chat import get_chat_client
from app.prompts.qa_prompt import build_qa_messages
from app.services.query_rewrite import rewrite
from app.services.retrieval_service import RetrievalError, RetrievedChunk, search_kb

logger = logging.getLogger(__name__)


class RagError(Exception):
    """RAG 管线失败（Chat 层应转 SSE error，不静默）。"""


#: 转人工关键词（规则式 intent，命中即短路，不检索不调 LLM）。
#: 仅保留明确短语，避免单字/通用词误命中（见 M6）。
HANDOFF_KEYWORDS = (
    "转人工", "人工客服", "人工服务", "找人工", "投诉", "经理", "真人客服",
)
#: 情绪/强烈不满词（T1 分流升级）：命中即 handoff（高优建单）。
#: 注意：`退款` 是 qa 高频词（Q005/Q024 退款到账为问答题），**不得**入此表；
#: `投诉` 已在 HANDOFF，不重复。情绪词与 HANDOFF 分离，保持语义单一。
EMOTIONAL_KEYWORDS = (
    "退钱", "赔偿", "太慢", "差评", "气死", "骗子", "欺诈",
    "立刻解决", "马上解决", "马上处理", "服务太差", "受不了", "垃圾", "投诉无门",
)
CHITCHAT_KEYWORDS = (
    "你好", "在吗", "谢谢", "再见", "你是谁", "你是机器人",
    "天气", "笑话", "几点下班", "下班", "心情",
)

#: M6：裸「人工」词边界匹配，但排除「人工智能」（避免误判 handoff）。
_RE_ARTIFICIAL = re.compile(r"人工(?!智能)")

#: 检索分数阈值在 app.core.config.settings.MIN_SCORE（单一真源，run_pipeline 直接读）


@dataclass
class RagResult:
    """管线产物：Chat 层据此发 SSE 事件。"""

    intent: str  # qa | handoff | chitchat
    chunks: list[RetrievedChunk] = field(default_factory=list)
    refuse: bool = False  # 无依据拒答（诚实性）
    refuse_reason: str = ""
    from_cache: bool = False  # T10：答案缓存命中（不走 LLM）
    cached_answer: str = ""  # T10：缓存答案全文
    cached_sources: list[dict] = field(default_factory=list)  # T10：缓存 sources（含 doc_title）
    rewritten_query: str = ""  # T9：检索/缓存 key 用的改写后文本


def classify_intent(query: str) -> str:
    """规则式意图分类：handoff(人工+情绪) > chitchat > qa（T1 分流升级：情绪词并入 handoff）。"""
    if _RE_ARTIFICIAL.search(query) or any(k in query for k in HANDOFF_KEYWORDS):
        return "handoff"
    if any(k in query for k in EMOTIONAL_KEYWORDS):
        return "handoff"
    if any(k in query for k in CHITCHAT_KEYWORDS):
        return "chitchat"
    return "qa"


def run_pipeline(query: str, kb_id: UUID, top_k: int = 8, history: list[dict] | None = None, kb_version: str | None = None) -> RagResult:
    """RAG 管线入口（非流式部分）：intent(原文) → 缓存/检索(改写后) → 拒答判定。

    顺序契约（T9-S3）：intent 恒用原文判定；改写只服务检索与缓存 key（query_rewrite）。
    T10：kb_version 由调用方（chat 层查 KB.updated_at）传入，缓存命中即返回（不走 LLM）。
    返回 RagResult，生成阶段由 Chat 层用 build_qa_messages 组装后流式调用。
    """
    intent = classify_intent(query)
    result = RagResult(intent=intent)

    if intent == "qa":
        # T9-S3：改写（检索/缓存 key 用）；intent 已用原文
        rewritten, _meta = rewrite(query, history)
        result.rewritten_query = rewritten
        try:
            # T10：缓存命中（精确+语义，实体锁定+KB 版本校验）→ 不走检索/LLM
            from app.services.answer_cache import get as cache_get

            cached = cache_get(rewritten, kb_version, kb_id=str(kb_id))
            if cached:
                result.from_cache = True
                result.cached_answer = cached.get("answer", "")
                result.cached_sources = cached.get("sources", [])
                logger.info("RAG 缓存命中: query=%s", rewritten[:40])
                return result

            chunks = search_kb(rewritten, kb_id, top_k=top_k)
        except RetrievalError as e:
            raise RagError(f"检索不可用: {e}") from e
        result.chunks = chunks
        # 诚实性：无依据拒答。hybrid 下 RRF 融合分无绝对语义（排序用），
        # 拒答独立看 dense 余弦分数（best dense——RRF 排序与相关性判定解耦，ADR-2026-08-16 §3.5）
        best_dense = max((c.dense_score for c in chunks), default=0.0) if chunks else 0.0
        if not chunks or best_dense < settings.MIN_SCORE:
            result.refuse = True
            result.refuse_reason = "未找到可靠依据"
        logger.info("RAG qa: best_dense=%.3f refuse=%s", best_dense, result.refuse)

    return result


async def stream_answer(
    query: str,
    kb_id: UUID,
    history: list[dict] | None = None,
    top_k: int = 8,
    kb_version: str | None = None,
):
    """流式回答：yield (event_type, data)。

    event_type: intent | stage | token | sources | done | error
    - intent{intent,refuse} → stage/retrieving → stage/generating → sources → token* → done
    - 拒答/闲聊/转人工 不发 token，直接 sources([])+done
    - T10 缓存命中：intent → stage* → token*(缓存答案分片) → sources(缓存) → done(cache_hit=true)
    - 任一异常 → error（fail-closed，不静默）
    """
    result = RagResult(intent="qa")
    try:
        # H2 修复：run_pipeline 内含阻塞式 embedding（model.encode），搬出事件循环
        # T9-S3：history 传入供指代消解（检索用改写，intent 用原文）；kb_version 供 T10 缓存校验
        result = await run_in_threadpool(
            run_pipeline, query, kb_id, top_k=top_k, history=history, kb_version=kb_version
        )
    except RagError as e:
        yield ("error", {"code": "RAG_RETRIEVAL", "message": str(e)})
        return

    # R-2：真实意图事件（chat 层据此落库 message.intent，不再写死 qa）
    yield ("intent", {"intent": result.intent, "refuse": result.refuse})
    yield ("stage", {"stage": "retrieving", "msg": "已检索知识库"})
    yield ("stage", {"stage": "generating", "msg": "正在生成回答"})

    # T10：缓存命中 → 直接分片输出缓存答案（省 LLM + 提速），sources 用缓存
    if result.from_cache:
        answer = result.cached_answer or ""
        for delta in _split_tokens(answer):
            yield ("token", {"delta": delta})
        yield ("sources", {"sources": result.cached_sources})
        yield ("done", {"message_id": "", "cache_hit": True})
        return

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
        # 不传 model：让 OpenAILikeChatClient 用自己的 _default_model()（provider-aware），
        # 避免 provider=zhipu 时把 settings.CHAT_MODEL（百炼名）打到智谱端点 → modelCode 不存在 → 400
        async for delta in client.stream(messages):
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
            # 字段名对齐前端契约 MessageSource.snippet（SSE 契约 §1.4）
            "snippet": c.text[:200],
        }
        for c in chunks
    ]


def _split_tokens(text: str, size: int = 8) -> list[str]:
    """非流式回复切成小片模拟流式（前端 SSE 展示流畅）。"""
    return [text[i : i + size] for i in range(0, len(text), size)]
