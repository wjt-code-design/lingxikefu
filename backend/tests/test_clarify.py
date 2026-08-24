"""Clarify 澄清问句生成测试（批次C）：prompt 结构 / 失败抛错 / 输出清洗。"""
from __future__ import annotations

import pytest
from app.services.clarify import ClarifyError, generate_clarify
from app.services.retrieval_service import RetrievedChunk


def _chunk(text: str) -> RetrievedChunk:
    return RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0, text=text, score=0.6)


class _FakeClient:
    def __init__(self, reply: str):
        self._reply = reply
        self.messages: list[dict] | None = None

    async def complete(self, messages, **kwargs):
        self.messages = messages
        return self._reply


class _BoomClient:
    async def complete(self, messages, **kwargs):
        raise RuntimeError("llm down")


async def test_generate_clarify_builds_prompt_with_candidates(monkeypatch):
    fake = _FakeClient("您是想咨询退货的退款到账时间，还是退货的申请流程？")
    monkeypatch.setattr("app.services.clarify.get_chat_client", lambda: fake)
    out = await generate_clarify("怎么退货", [_chunk("退货申请流程……"), _chunk("退款 1-3 个工作日……")])
    assert out == "您是想咨询退货的退款到账时间，还是退货的申请流程？"
    sys_msg = fake.messages[0]["content"]
    user_msg = fake.messages[1]["content"]
    assert "澄清" in sys_msg
    assert "怎么退货" in user_msg
    assert "退货申请流程" in user_msg  # 候选片段进 prompt
    assert "二选一" in sys_msg or "两个选项" in sys_msg  # 约束：二选一式


async def test_generate_clarify_failure_raises(monkeypatch):
    monkeypatch.setattr("app.services.clarify.get_chat_client", lambda: _BoomClient())
    with pytest.raises(ClarifyError):
        await generate_clarify("怎么退货", [_chunk("x")])


async def test_generate_clarify_strips_output(monkeypatch):
    """输出清洗：去首尾空白/引号/前缀话术（LLM 常见噪声）。"""
    fake = _FakeClient('  "好的，请问：您是想查物流还是查退款？" \n')
    monkeypatch.setattr("app.services.clarify.get_chat_client", lambda: fake)
    out = await generate_clarify("q", [_chunk("x")])
    assert out == "您是想查物流还是查退款？"
    assert not out.startswith('"')


async def test_generate_clarify_no_chunks_still_works(monkeypatch):
    """拒答场景可能 0 候选（降噪过滤后全空）——prompt 无候选段也要能生成。"""
    fake = _FakeClient("请问您具体想咨询哪方面的问题呢？")
    monkeypatch.setattr("app.services.clarify.get_chat_client", lambda: fake)
    out = await generate_clarify("q", [])
    assert out


async def test_generate_clarify_empty_output_raises(monkeypatch):
    """清洗后为空 → 视为失败抛 ClarifyError（不能发空问句给用户）。"""
    fake = _FakeClient('""')
    monkeypatch.setattr("app.services.clarify.get_chat_client", lambda: fake)
    with pytest.raises(ClarifyError):
        await generate_clarify("q", [_chunk("x")])
