"""Clarify 澄清问句生成测试（批次C）：prompt 结构 / 失败抛错 / 输出清洗。"""
from __future__ import annotations

import app.services.rag_service as rag_service
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


# ---------- stream_answer 拒答分支集成（批次C Task 2） ----------


class _RefusePipeline:
    """强制拒答的 run_pipeline 替身：refuse=True 且给候选片段。"""

    @staticmethod
    def run(query, kb_id, top_k=None, history=None, kb_version=None):
        return rag_service.RagResult(
            intent="qa",
            chunks=[_chunk("退货申请流程……"), _chunk("退款到账说明……")],
            refuse=True,
            refuse_reason="未找到可靠依据",
        )


async def _collect(ag):
    out = []
    async for e, d in ag:
        out.append((e, d))
    return out


async def test_stream_answer_clarify_when_allowed(monkeypatch):
    """refuse + clarify_left=1 → 澄清问句流：intent(qa/false) + token(问句) + done(clarify=True)。"""
    monkeypatch.setattr(rag_service, "run_pipeline", _RefusePipeline.run)
    monkeypatch.setattr(
        "app.services.rag_service.generate_clarify",
        lambda q, chunks: _AsyncVal("您是想咨询退货流程还是退款到账？"),
    )
    events = await _collect(
        rag_service.stream_answer("怎么退货", __import__("uuid").uuid4(), clarify_left=1)
    )
    kinds = [e for e, _ in events]
    assert kinds[0] == "intent"
    assert events[0][1] == {"intent": "qa", "refuse": False}  # 澄清轮不标记拒答
    assert "token" in kinds
    tokens = "".join(d["delta"] for e, d in events if e == "token")
    assert "退货流程" in tokens or "退款到账" in tokens
    done = [d for e, d in events if e == "done"][-1]
    assert done.get("clarify") is True


async def test_stream_answer_refuse_when_no_quota_left(monkeypatch):
    """clarify_left=0（或 None）→ 原拒答路径不变（fail-open 兼容旧调用）。"""
    monkeypatch.setattr(rag_service, "run_pipeline", _RefusePipeline.run)
    events = await _collect(
        rag_service.stream_answer("怎么退货", __import__("uuid").uuid4(), clarify_left=0)
    )
    intent_ev = events[0]
    assert intent_ev[1]["refuse"] is True  # 原拒答语义
    done = [d for e, d in events if e == "done"][-1]
    assert "clarify" not in done
    tokens = "".join(d["delta"] for e, d in events if e == "token")
    assert "转人工" in tokens  # 原拒答文案


async def test_stream_answer_clarify_error_falls_back(monkeypatch):
    """generate_clarify 抛错 → 落回原拒答（不产生半截流）。"""
    monkeypatch.setattr(rag_service, "run_pipeline", _RefusePipeline.run)
    monkeypatch.setattr(
        "app.services.rag_service.generate_clarify",
        lambda q, chunks: _AsyncRaise(rag_service.ClarifyError("boom")),
    )
    events = await _collect(
        rag_service.stream_answer("怎么退货", __import__("uuid").uuid4(), clarify_left=2)
    )
    assert events[0][1]["refuse"] is True
    done = [d for e, d in events if e == "done"][-1]
    assert "clarify" not in done


class _AsyncVal:
    """最小 awaitable 替身：bare yield（挂起值 None，asyncio Task 合法）+ return v。

    注：`__await__` 内不可 `yield v`（非 None 挂起值穿透到 asyncio Task 会抛
    "Task got bad yield"）——await 的结果必须经 StopIteration.value 返回。
    """

    def __init__(self, v):
        self._v = v

    def __await__(self):
        yield
        return self._v


class _AsyncRaise:
    def __init__(self, e):
        self._e = e

    def __await__(self):
        raise self._e
        yield  # pragma: no cover
