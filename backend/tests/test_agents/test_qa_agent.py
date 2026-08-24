"""QA Agent 测试：非流式管线封装 + 边界守卫。

设计纪律（对齐 test_agent_behavior）：仅 mock 检索（不依赖真实 Qdrant）；
rewrite 是纯规则函数无需 mock；断言走真实编排路径（run_pipeline →
PipelineRunner 节点），而非绕过节点直调函数（教训库：测试必须走真实路径）。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from app.services.agents.qa_agent import QAAgent
from app.services.retrieval_service import RetrievedChunk
from app.services.shared_context import SharedContext


def make_chunk(score: float, text: str = "保修条款内容", doc_id: str = "d1") -> RetrievedChunk:
    return RetrievedChunk(chunk_id="c1", doc_id=doc_id, kb_id="kb1", idx=0, text=text, score=score, dense_score=score)


@pytest.fixture(autouse=True)
def _patch_search(monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval_service.search_kb",
        lambda q, kb, top_k=5: [make_chunk(0.9)],
    )


def test_run_returns_rag_result():
    ctx = SharedContext(query="保修多久", kb_id=uuid4())
    ctx = QAAgent().run(ctx)
    assert ctx.rag_result is not None
    assert ctx.rag_result.intent == "qa"
    assert not ctx.rag_result.refuse
    assert len(ctx.rag_result.chunks) == 1


def test_handoff_intent_short_circuits_retrieval():
    """handoff 走管线短路：不检索（与 PipelineRunner 短路行为一致）。"""
    ctx = SharedContext(query="我要投诉找经理", kb_id=uuid4())
    ctx = QAAgent().run(ctx)
    assert ctx.rag_result is not None
    assert ctx.rag_result.intent == "handoff"
    assert ctx.rag_result.chunks == []  # 短路：未检索


def test_fused_query_used_when_present(monkeypatch):
    """图片通道预留：fused_query 存在时优先于原文进入管线（检索/缓存键同源）。"""
    seen = []

    def _fake_search(q, kb, top_k=5):
        seen.append(q)
        return [make_chunk(0.9)]

    monkeypatch.setattr("app.services.retrieval_service.search_kb", _fake_search)
    ctx = SharedContext(query="这是什么", kb_id=uuid4())
    ctx.fused_query = "屏幕碎裂的手机图片 保修查询"
    ctx = QAAgent().run(ctx)
    assert seen and "屏幕碎裂" in seen[0]  # 检索用的是融合后文本


def test_no_kb_degraded_with_trace():
    ctx = SharedContext(query="保修多久", kb_id=None)
    ctx = QAAgent().run(ctx)
    assert ctx.rag_result is None
    assert "qa:no_kb" in ctx.degraded
