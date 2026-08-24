"""PipelineRunner 集成测试：真实节点 + mock 外部依赖。

验证数据流：intent → rewrite → cache_check → retrieve → refuse，
以及条件短路（intent != qa / from_cache）。
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.orchestrator import PipelineRunner
from app.services.pipeline import Pipeline
from app.services.steps.cache_check import check_cache
from app.services.steps.intent import classify_intent
from app.services.steps.refuse import check_refuse
from app.services.steps.retrieve import retrieve_chunks
from app.services.steps.rewrite import rewrite_query


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_pipeline(query: str = "退货运费谁出") -> Pipeline:
    return Pipeline(query=query, kb_id=uuid4())


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_search(monkeypatch):
    """Mock retrieval_service.search_kb 返回可控的 chunk 列表."""
    captured = {"query": None, "top_k": None}

    def _fake(q, kb, top_k=5):
        captured["query"] = q
        captured["top_k"] = top_k
        return []

    monkeypatch.setattr("app.services.retrieval_service.search_kb", _fake)
    return captured


@pytest.fixture
def mock_cache(monkeypatch):
    """Mock answer_cache.get 默认返回 None（miss）."""
    monkeypatch.setattr("app.services.answer_cache.get", lambda *a, **kw: None)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestFullPipelineWithRealNodes:
    """真实节点通过 PipelineRunner 编排——数据流端到端验证。"""

    def test_qa_flow_runs_all_nodes(self, mock_search, mock_cache):
        """qa 意图：5 个节点全部执行，数据正确传递。"""
        mock_search["query"] = None  # 重置

        def _fake(q, kb, top_k=5):
            mock_search["query"] = q
            from app.services.retrieval_service import RetrievedChunk
            return [RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0,
                                    text="退货运费由卖家承担", score=0.9, dense_score=0.9)]

        # 重新 patch（fixture 里的 lambda 无法覆盖，直接 setattr）
        import app.services.retrieval_service as rs
        rs.search_kb = _fake

        runner = PipelineRunner(retry=0)
        pipeline = _make_pipeline("退货运费谁出")
        result = runner.run(
            pipeline,
            [classify_intent, rewrite_query, check_cache, retrieve_chunks, check_refuse],
        )

        assert result.intent == "qa"
        assert result.rewritten_query == "退货运费谁出"  # 无口语，改写不变
        assert result.from_cache is False
        assert len(result.chunks) == 1
        assert result.refuse is False
        assert mock_search["query"] == "退货运费谁出"  # 检索用改写后文本

    def test_handoff_skips_retrieval_nodes(self, mock_search, mock_cache):
        """handoff 意图：intent 节点后，后续节点全部跳过（条件短路）。"""
        runner = PipelineRunner(retry=0)
        pipeline = _make_pipeline("我要投诉找人工")
        result = runner.run(
            pipeline,
            [classify_intent, rewrite_query, check_cache, retrieve_chunks, check_refuse],
        )

        assert result.intent == "handoff"
        # 检索节点未执行 → search_kb 未被调用
        assert mock_search["query"] is None

    def test_cache_hit_skips_retrieval(self, mock_search, mock_cache):
        """缓存命中：from_cache=True 后跳过 retrieve/refuse。"""
        # 让 cache_check 命中
        import app.services.answer_cache as ac
        ac.get = lambda *a, **kw: {
            "answer": "退货运费由卖家承担",
            "sources": [{"doc_title": "退换货政策"}],
        }

        runner = PipelineRunner(retry=0)
        pipeline = _make_pipeline("退货运费谁出")
        result = runner.run(
            pipeline,
            [classify_intent, rewrite_query, check_cache, retrieve_chunks, check_refuse],
        )

        assert result.intent == "qa"
        assert result.from_cache is True
        assert result.cached_answer == "退货运费由卖家承担"
        # retrieve 未执行
        assert mock_search["query"] is None

    def test_low_score_triggers_refuse(self, mock_search, mock_cache):
        """检索分数低于阈值 → refuse=True。"""
        from app.services.retrieval_service import RetrievedChunk
        import app.services.retrieval_service as rs

        def _fake(q, kb, top_k=5):
            return [RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0,
                                    text="无关内容", score=0.1, dense_score=0.1)]

        rs.search_kb = _fake

        runner = PipelineRunner(retry=0)
        pipeline = _make_pipeline("星河的创始人是谁")
        result = runner.run(
            pipeline,
            [classify_intent, rewrite_query, check_cache, retrieve_chunks, check_refuse],
        )

        assert result.intent == "qa"
        assert result.refuse is True
        assert result.refuse_reason == "未找到可靠依据"

    def test_rewrite_output_feeds_retrieval(self, mock_search, mock_cache):
        """改写输出作为检索输入（T9-S3 契约）。"""
        from app.services.retrieval_service import RetrievedChunk
        import app.services.retrieval_service as rs

        def _fake(q, kb, top_k=5):
            mock_search["query"] = q
            return [RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0,
                                    text="碎屏险更换流程", score=0.9, dense_score=0.9)]

        rs.search_kb = _fake

        runner = PipelineRunner(retry=0)
        pipeline = _make_pipeline("碎屏显咋换")
        result = runner.run(
            pipeline,
            [classify_intent, rewrite_query, check_cache, retrieve_chunks, check_refuse],
        )

        assert result.intent == "qa"
        # 检索入参是改写后的文本，不是原文
        assert mock_search["query"] == "碎屏险怎么换"
        assert result.rewritten_query == "碎屏险怎么换"

    def test_stage_log_tracks_execution_order(self, mock_search, mock_cache):
        """stages 记录节点执行顺序，用于排障。"""
        from app.services.retrieval_service import RetrievedChunk
        import app.services.retrieval_service as rs

        def _fake(q, kb, top_k=5):
            return [RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0,
                                    text="test", score=0.9, dense_score=0.9)]

        rs.search_kb = _fake

        runner = PipelineRunner(retry=0)
        pipeline = _make_pipeline("退货政策")
        result = runner.run(
            pipeline,
            [classify_intent, rewrite_query, check_cache, retrieve_chunks, check_refuse],
        )

        stage_names = [s["name"] for s in result.stages]
        assert stage_names == ["intent", "rewrite", "cache_check", "retrieve", "refuse_check"]

    def test_to_dict_serialization_excludes_chunks(self, mock_search, mock_cache):
        """to_dict() 不含 chunks 大数组，只保留长度。"""
        from app.services.retrieval_service import RetrievedChunk
        import app.services.retrieval_service as rs

        def _fake(q, kb, top_k=5):
            return [RetrievedChunk(chunk_id="c1", doc_id="d1", kb_id="kb1", idx=0,
                                    text="test", score=0.9, dense_score=0.9)]

        rs.search_kb = _fake

        runner = PipelineRunner(retry=0)
        pipeline = _make_pipeline("退货政策")
        result = runner.run(
            pipeline,
            [classify_intent, rewrite_query, check_cache, retrieve_chunks, check_refuse],
        )

        d = result.to_dict()
        assert "chunks" not in d
        assert d["retrieved_chunks"] == 1
        assert d["intent"] == "qa"
        assert d["errors"] == []
