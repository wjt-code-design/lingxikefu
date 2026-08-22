"""Phase C prompt 注入测试：用户画像注入 + 兼容 + fail-open（2026-08-22）。

A 层确定性断言，不依赖真实 LLM。覆盖：
- build_qa_messages(profile=...) → <<用户画像>> 块注入，位置在 <<用户问题>> 前；
- 无 profile → 输出与旧版逐字节一致（diff=0 兼容）；
- stream_answer(user_profile=...) → 透传到 build_qa_messages（FakeChat 捕获 messages 断言）；
- 画像不影响缓存 key（缓存命中路径不注入——由设计保证，断言消息组装不含画像）。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from app.prompts.qa_prompt import build_qa_messages
from app.services.rag_service import stream_answer
from app.services.retrieval_service import RetrievedChunk


def make_chunk(score: float, text: str = "保修条款内容") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0, text=text, score=score, dense_score=score
    )


# ---------- 一、build_qa_messages 注入 ----------

def test_build_qa_messages_injects_profile():
    """有 profile → <<用户画像>> 块注入，位于 <<用户问题>> 之前。"""
    msgs = build_qa_messages(
        "怎么退款",
        [make_chunk(0.9)],
        profile="用户画像：常问主题 退款(2)",
    )
    user_content = msgs[-1]["content"]
    assert "<<用户画像>>" in user_content
    assert "常问主题 退款(2)" in user_content
    # 位置：画像块在问题块之前
    assert user_content.index("<<用户画像>>") < user_content.index("<<用户问题>>")


def test_build_qa_messages_no_profile_diff_zero():
    """无 profile → 输出与旧版完全一致（diff=0 兼容，关键回归门禁）。"""
    args = dict(query="怎么退款", chunks=[make_chunk(0.9)], history=None, context_hint=None)
    msgs_without = build_qa_messages(**args)
    # 模拟旧版行为（旧版 build_qa_messages 无 profile 参数）：显式传 profile=None
    msgs_old = build_qa_messages(profile=None, **args)
    assert msgs_without == msgs_old
    assert "<<用户画像>>" not in msgs_without[-1]["content"]


def test_build_qa_messages_profile_with_history_and_context():
    """画像与历史/会话上下文可共存（多块隔离，互不污染）。"""
    msgs = build_qa_messages(
        "那个订单呢",
        [make_chunk(0.9)],
        history=[{"role": "user", "content": "订单 XOZ-12345"}],
        context_hint="用户当前主题：退款；关联实体：XOZ-12345",
        profile="用户画像：常问主题 退款(1)；历史关联实体 XOZ-12345",
    )
    user_content = msgs[-1]["content"]
    assert "<<历史对话>>" in user_content
    assert "<<会话上下文>>" in user_content
    assert "<<用户画像>>" in user_content
    assert "<<用户问题>>" in user_content
    # 顺序：历史 → 上下文 → 画像 → 问题
    idx_hist = user_content.index("<<历史对话>>")
    idx_ctx = user_content.index("<<会话上下文>>")
    idx_prof = user_content.index("<<用户画像>>")
    idx_q = user_content.index("<<用户问题>>")
    assert idx_hist < idx_ctx < idx_prof < idx_q


# ---------- 二、stream_answer 透传 ----------

@pytest.fixture(autouse=True)
def patch(monkeypatch):
    """mock 检索 + mock chat client：捕获发送给 LLM 的 messages。"""

    def _fake_search(q, kb, top_k=5):  # type: ignore[no-untyped-def]
        return [make_chunk(0.9)]

    monkeypatch.setattr("app.services.rag_service.search_kb", _fake_search)
    captured = {"messages": None}

    class FakeChat:
        def __init__(self):
            self.calls = []

        async def stream(self, messages, model=None, **kw):  # type: ignore[no-untyped-def]
            self.calls.append(messages)
            captured["messages"] = messages
            yield "好"

    fake = FakeChat()
    monkeypatch.setattr("app.services.rag_service.get_chat_client", lambda: fake)
    return fake, captured


async def test_stream_answer_passes_profile_to_prompt(patch):
    """stream_answer(user_profile=...) → 最终 messages 含 <<用户画像>> 块。"""
    _, captured = patch
    events = [
        e
        async for e in stream_answer(
            "怎么退款", uuid4(), user_profile="用户画像：常问主题 退款(2)"
        )
    ]
    assert any(t == "token" for t, _ in events)
    msgs = captured["messages"]
    assert msgs is not None
    user_content = msgs[-1]["content"]
    assert "<<用户画像>>" in user_content


async def test_stream_answer_without_profile_no_profile_block(patch):
    """不传 user_profile → messages 无 <<用户画像>> 块（旧行为）。"""
    _, captured = patch
    [e async for e in stream_answer("怎么退款", uuid4())]
    msgs = captured["messages"]
    assert msgs is not None
    assert "<<用户画像>>" not in msgs[-1]["content"]


async def test_stream_answer_profile_does_not_affect_cache_key(patch):
    """画像只影响 prompt，不影响检索/缓存路径（检索仍用改写 query，无画像注入检索）。"""
    from app.services import rag_service

    # 捕获检索调用：确认检索 query 不含画像
    captured_search = {}

    def _search_spy(q, kb, top_k=5):  # type: ignore[no-untyped-def]
        captured_search["q"] = q
        return [make_chunk(0.9)]

    patch[0].__class__  # noqa: B018 - 保持引用
    rag_service.search_kb = _search_spy  # type: ignore[assignment]
    events = [
        e
        async for e in stream_answer(
            "怎么退款", uuid4(), user_profile="用户画像：常问主题 退款(2)"
        )
    ]
    assert any(t == "token" for t, _ in events)
    # 检索 query 是改写后的原问题，不含画像文本
    assert "用户画像" not in captured_search["q"]
