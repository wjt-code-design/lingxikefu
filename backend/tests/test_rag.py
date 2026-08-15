"""RAG 管线测试（BU-03）：intent 分类 / 检索接线 / 诚实性拒答 / prompt / 流式事件。

- mock 检索与 chat client：不依赖真实 Qdrant/百炼；
- 重点验证：无依据拒答（fail-closed）、SSE 事件顺序、prompt 含来源。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from app.prompts.qa_prompt import build_qa_messages
from app.services.rag_service import (
    RagError,
    classify_intent,
    run_pipeline,
    stream_answer,
)
from app.services.retrieval_service import RetrievedChunk


def make_chunk(score: float, text: str = "保修条款内容", doc_id: str = "d1") -> RetrievedChunk:
    return RetrievedChunk(chunk_id="c1", doc_id=doc_id, kb_id="kb1", idx=0, text=text, score=score)


class FakeChat:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, model=None, **kw):
        self.calls.append((messages, model))
        for c in "你好":
            yield c


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    monkeypatch.setattr(
        "app.services.rag_service.search_kb", lambda q, kb, top_k=5: [make_chunk(0.9)]
    )
    monkeypatch.setattr("app.services.rag_service.settings.CHAT_MODEL", "qwen3.7-flash", raising=False)
    fake = FakeChat()
    monkeypatch.setattr("app.services.rag_service.get_chat_client", lambda: fake)
    return fake


# --- intent 分类 ---
def test_intent_handoff_keyword():
    assert classify_intent("我要找人工客服投诉") == "handoff"


def test_intent_chitchat_keyword():
    assert classify_intent("你好呀") == "chitchat"


def test_intent_qa_default():
    assert classify_intent("退货运费谁出") == "qa"


# --- 管线 ---
def test_pipeline_qa_retrieves_chunks(patch):
    r = run_pipeline("退货运费谁出", uuid4())
    assert r.intent == "qa"
    assert len(r.chunks) == 1 and not r.refuse


def test_pipeline_low_score_refuses(patch, monkeypatch):
    """诚实性：top-1 分数低于阈值 → 拒答，不编造。"""
    monkeypatch.setattr(
        "app.services.rag_service.search_kb", lambda q, kb, top_k=5: [make_chunk(0.1)]
    )
    r = run_pipeline("星河的创始人是谁", uuid4())
    assert r.refuse is True


def test_pipeline_no_chunks_refuses(patch, monkeypatch):
    monkeypatch.setattr(
        "app.services.rag_service.search_kb", lambda q, kb, top_k=5: []
    )
    r = run_pipeline("完全无关的问题", uuid4())
    assert r.refuse is True


def test_pipeline_retrieval_error_raises(patch, monkeypatch):
    from app.services.retrieval_service import RetrievalError

    def boom(*_a, **_k):
        raise RetrievalError("Qdrant 挂了")

    monkeypatch.setattr("app.services.rag_service.search_kb", boom)
    with pytest.raises(RagError, match="检索不可用"):
        run_pipeline("退货运费", uuid4())


# --- prompt ---
def test_build_qa_messages_contains_sources():
    msgs = build_qa_messages("保修多久", [make_chunk(0.9, "保修期12个月"), make_chunk(0.8, "电池6个月")])
    assert msgs[0]["role"] == "system"
    assert "[来源1]" in msgs[0]["content"] and "[来源2]" in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "保修多久"}


def test_build_qa_messages_history():
    msgs = build_qa_messages("那电池呢", [make_chunk(0.9)], history=[{"role": "user", "content": "保修多久"}, {"role": "assistant", "content": "12个月"}])
    assert "用户: 保修多久" in msgs[0]["content"]
    assert "客服: 12个月" in msgs[0]["content"]


# --- 流式事件 ---
async def test_stream_answer_events_order(patch):
    events = [e async for e in stream_answer("保修多久", uuid4())]
    types = [t for t, _ in events]
    # 顺序：stage retrieving → stage generating → token* → sources → done
    assert types[0] == "stage" and types[1] == "stage"
    assert "token" in types
    assert types[-2] == "sources" and types[-1] == "done"


async def test_stream_answer_handoff_no_llm(patch):
    events = [e async for e in stream_answer("我要投诉找人工", uuid4())]
    types = [t for t, _ in events]
    assert "token" in types and types[-1] == "done"
    # handoff 不调 LLM
    assert patch.calls == []


async def test_stream_answer_refuse_no_llm(patch, monkeypatch):
    monkeypatch.setattr(
        "app.services.rag_service.search_kb", lambda q, kb, top_k=5: [make_chunk(0.1)]
    )
    events = [e async for e in stream_answer("星河的创始人是谁", uuid4())]
    types = [t for t, _ in events]
    assert types[-1] == "done"
    assert patch.calls == []  # 拒答不调 LLM


async def test_stream_answer_error_event(patch, monkeypatch):
    from app.services.retrieval_service import RetrievalError

    def boom(*_a, **_k):
        raise RetrievalError("down")

    monkeypatch.setattr("app.services.rag_service.search_kb", boom)
    events = [e async for e in stream_answer("退货运费", uuid4())]
    assert events[0][0] == "error"
    assert events[0][1]["code"] == "RAG_RETRIEVAL"
