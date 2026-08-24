"""PipelineRunner 编排核心测试：覆盖执行、重试、条件短路。"""
from __future__ import annotations

from uuid import uuid4

import pytest
from app.orchestrator import PipelineRunner
from app.services.pipeline import Pipeline

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_pipeline() -> Pipeline:
    return Pipeline(query="退货政策是什么", kb_id=uuid4())


def _mk_node(name: str, *, fail_times: int = 0, side_effect=None):
    """创建一个记录调用次数的模拟节点。"""
    call_count = {"n": 0}

    def _node(pipeline: Pipeline) -> Pipeline:
        call_count["n"] += 1
        if call_count["n"] <= fail_times:
            raise RuntimeError(f"{name} transient error #{call_count['n']}")
        pipeline.add_stage(name)
        if side_effect:
            side_effect(pipeline)
        return pipeline

    _node.call_count = call_count  # type: ignore[attr-defined]
    _node.__name__ = name
    return _node


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestPipelineRunner:
    """PipelineRunner 正常执行。"""

    def test_runs_all_nodes_in_order(self, base_pipeline: Pipeline):
        n1 = _mk_node("node_a")
        n2 = _mk_node("node_b")
        runner = PipelineRunner(retry=0)
        result = runner.run(base_pipeline, [n1, n2])
        assert [s["name"] for s in result.stages] == ["node_a", "node_b"]

    def test_short_circuits_when_intent_not_qa(self, base_pipeline: Pipeline):
        pipeline = base_pipeline
        pipeline.intent = "handoff"  # 非 qa → 后续节点全跳

        n_called = _mk_node("should_skip")
        runner = PipelineRunner(retry=0)
        runner.run(pipeline, [n_called])
        assert n_called.call_count["n"] == 0  # 从未被调用


class TestPipelineRunnerRetry:
    """节点失败重试。"""

    def test_retries_failed_node_once(self, base_pipeline: Pipeline):
        n = _mk_node("flaky", fail_times=1)
        runner = PipelineRunner(retry=1)
        runner.run(base_pipeline, [n])
        assert n.call_count["n"] == 2  # 失败 1 + 成功 1

    def test_raises_after_max_retries(self, base_pipeline: Pipeline):
        n = _mk_node("always_fail", fail_times=99)
        runner = PipelineRunner(retry=1)
        with pytest.raises(RuntimeError, match="always_fail"):
            runner.run(base_pipeline, [n])
        assert n.call_count["n"] == 2  # 初始 1 + 重试 1

    def test_no_retry_on_success(self, base_pipeline: Pipeline):
        n = _mk_node("ok", fail_times=0)
        runner = PipelineRunner(retry=1)
        runner.run(base_pipeline, [n])
        assert n.call_count["n"] == 1


class TestPipelineSerialization:
    """Pipeline.to_dict() 序列化。"""

    def test_to_dict_excludes_large_arrays(self):
        pipeline = Pipeline(
            query="test query",
            kb_id=uuid4(),
            intent="qa",
            dense_scores=[0.12345678, 0.23456789, 0.34567890],
            chunks=["x"] * 10,
        )
        pipeline.add_stage("intent")
        d = pipeline.to_dict()
        assert d["query"] == "test query"
        assert d["intent"] == "qa"
        # dense_scores 截断到 5 个并保留 4 位小数
        assert d["dense_scores"] == [0.1235, 0.2346, 0.3457]
        # chunks 只保留长度，不含内容
        assert d["retrieved_chunks"] == 10
        assert "chunks" not in d
        assert [s["name"] for s in d["stages"]] == ["intent"]
