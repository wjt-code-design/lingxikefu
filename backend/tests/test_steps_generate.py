"""steps/generate.py 单测：LLM 流式生成节点。

覆盖：消息构建传给客户端、多 delta 拼接、stage 记录、参数透传。
不触发真实网络：mock get_chat_client / build_qa_messages / client.stream。
"""
from __future__ import annotations

import asyncio
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from app.services.pipeline import Pipeline
from app.services.retrieval_service import RetrievedChunk
from app.services.steps.generate import generate_answer

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pipeline(*, chunks=None, history=None) -> Pipeline:
    return Pipeline(
        query="退货规则是什么",
        kb_id=uuid4(),
        chunks=chunks or [],
        history=history or [],
    )


def _chunk(text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        doc_id="d1",
        kb_id="kb1",
        idx=0,
        text=text,
        score=0.8,
    )


class _FakeStream:
    """Async context manager + async generator：yield 若干 delta，可注入异常。"""

    def __init__(self, deltas: list[str] | None = None, exc: Exception | None = None):
        self.deltas = ["你好", "，有问题", "请讲"] if deltas is None else deltas
        self.exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        if self.exc:
            raise self.exc
        for d in self.deltas:
            yield d


def _fake_client(stream_obj) -> Mock:
    """构造 mock 聊天客户端：stream 是异步生成器函数（调用即返回生成器，不 await）。"""
    client = Mock()
    client.stream = Mock(return_value=stream_obj)
    return client


# ---------------------------------------------------------------------------
# 正常流式拼接
# ---------------------------------------------------------------------------


def test_concatenates_stream_deltas():
    pipe = _pipeline(chunks=[_chunk("资料原文一")])
    fake_client = _fake_client(_FakeStream(["答案", "片段", "组合"]))
    seen_messages = {}

    def _fake_build(query, chunks, history, **kw):
        seen_messages["query"] = query
        seen_messages["chunk_count"] = len(chunks)
        return [{"role": "system", "content": "sys"}, {"role": "user", "content": query}]

    with (
        patch("app.llm_clients.chat.get_chat_client", return_value=fake_client),
        patch("app.prompts.qa_prompt.build_qa_messages", side_effect=_fake_build),
    ):
        result = asyncio.run(generate_answer(pipe))

    assert result is pipe
    assert pipe.final_answer == "答案片段组合"
    fake_client.stream.assert_called_once()
    # 传给 stream 的 messages 来自 build_qa_messages 的返回值
    msgs = fake_client.stream.call_args.args[0]
    assert msgs == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "退货规则是什么"},
    ]
    # 消息构建输入的 query / chunks 均已传递
    assert seen_messages["query"] == "退货规则是什么"
    assert seen_messages["chunk_count"] == 1
    assert [s["name"] for s in pipe.stages] == ["generate"]


def test_empty_stream_produces_empty_answer():
    pipe = _pipeline()
    fake_client = _fake_client(_FakeStream([]))
    with patch("app.llm_clients.chat.get_chat_client", return_value=fake_client):
        asyncio.run(generate_answer(pipe))

    assert pipe.final_answer == ""
    assert [s["name"] for s in pipe.stages] == ["generate"]


def test_system_injection_built_with_real_qa_prompt():
    """不 mock build_qa_messages：验证真实 prompt 组装含资料与历史、防注入分隔块。"""
    pipe = _pipeline(
        chunks=[_chunk("退货政策原文"), _chunk("发票规则原文")],
        history=[{"role": "user", "content": "上一轮问题"}, {"role": "assistant", "content": "上一轮回答"}],
    )
    fake_client = _fake_client(_FakeStream(["ok"]))
    with patch("app.llm_clients.chat.get_chat_client", return_value=fake_client):
        asyncio.run(generate_answer(pipe))

    msgs = fake_client.stream.call_args.args[0]
    assert len(msgs) == 2
    sys_content = msgs[0]["content"]
    user_content = msgs[1]["content"]
    # 资料带来源编号注入系统提示
    assert "[来源1] 退货政策原文" in sys_content
    assert "[来源2] 发票规则原文" in sys_content
    # 用户块含分隔的历史与问题（M10 防注入）
    assert "<<历史对话>>" in user_content
    assert "用户: 上一轮问题" in user_content
    assert "<<用户问题>>" in user_content
    assert "退货规则是什么" in user_content


# ---------------------------------------------------------------------------
# 异常传播
# ---------------------------------------------------------------------------


def test_stream_exception_propagates():
    """流式异常必须向上抛（由上层节点重试/降级），不得吞掉生成空答案。"""
    pipe = _pipeline()
    fake_client = _fake_client(_FakeStream(exc=RuntimeError("上游限流")))
    with (
        patch("app.llm_clients.chat.get_chat_client", return_value=fake_client),
        pytest.raises(RuntimeError, match="上游限流"),
    ):
        asyncio.run(generate_answer(pipe))

    # 异常时不得记 generate 成功 stage
    assert pipe.stages == []
