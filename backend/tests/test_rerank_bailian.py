"""百炼原生 rerank 客户端与管线节点的测试（2026-10-27）。

覆盖：
1. **端点路径**：必须是原生 DashScope 路径，不是 compatible-mode（404 空 body 坑）
2. **请求体结构**：input.query/input.documents 嵌套 + parameters.top_n
3. **响应解析**：output.results[].index/relevance_score；index 是**原始下标**
4. **错误语义**：HTTP 错误 / 业务错误(code) / 结构异常 → RerankError（fail-closed）
5. **管线节点**：dense_score 保留、fail-open 降级、空结果不丢 chunks
6. **不污染生产**：不调用真实网络（httpx.post 被 monkeypatch）
"""
from __future__ import annotations

import httpx
import pytest
from app.core.config import settings
from app.llm_clients.base import ModelNotConfiguredError
from app.llm_clients.rerank import get_rerank_client
from app.llm_clients.rerank_bailian import RerankError, rerank
from app.services.pipeline import Pipeline
from app.services.steps.rerank import rerank_chunks


class _FakeChunk:
    """最小 RetrievedChunk 替身（只需 text/chunk_id/dense_score）。"""

    def __init__(self, chunk_id: str, text: str, dense_score: float) -> None:
        self.chunk_id = chunk_id
        self.text = text
        self.dense_score = dense_score


def _patch_post(monkeypatch, status: int = 200, payload: dict | None = None, capture: dict | None = None):
    """把 httpx.Client.post 换成假实现。"""

    def fake_post(self, url, json=None, headers=None, **kwargs):  # noqa: ANN001, ARG001
        if capture is not None:
            capture["url"] = url
            capture["json"] = json
            capture["headers"] = headers
        body = payload if payload is not None else {"output": {"results": []}}
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", fake_post)


class TestRerankEndpoint:
    def test_uses_native_dashscope_path_not_compatible_mode(self, monkeypatch):
        """⚠️ 核心防回归：必须是原生路径。

        打 compatible-mode/v1/rerank 返回 HTTP 404 + 空 body（无错误说明），
        是最难排查的一类故障——此处钉死路径形态。
        """
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://ws-xxx.cn-beijing.maas.aliyuncs.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        cap: dict = {}
        _patch_post(monkeypatch, 200, {"output": {"results": [{"index": 0, "relevance_score": 0.9}]}}, cap)

        rerank("q", ["doc"])

        assert cap["url"] == (
            "https://ws-xxx.cn-beijing.maas.aliyuncs.com"
            "/api/v1/services/rerank/text-rerank/text-rerank"
        )
        assert "compatible-mode" not in cap["url"]

    def test_base_url_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://ws-xxx.cn-beijing.maas.aliyuncs.com/")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        cap: dict = {}
        _patch_post(monkeypatch, 200, {"output": {"results": []}}, cap)

        rerank("q", ["doc"])

        assert not cap["url"].endswith("//api/v1")

    def test_missing_base_url_raises_actionable_error(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        with pytest.raises(ModelNotConfiguredError, match="RERANK_BASE_URL"):
            rerank("q", ["doc"])

    def test_missing_api_key_raises_actionable_error(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "")
        with pytest.raises(ModelNotConfiguredError, match="API Key"):
            rerank("q", ["doc"])


class TestRerankRequest:
    def test_request_body_structure(self, monkeypatch):
        """请求体必须是 DashScope 原生嵌套结构。"""
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        monkeypatch.setattr(settings, "RERANK_MODEL", "qwen3.7-text-rerank")
        monkeypatch.setattr(settings, "RERANK_INSTRUCT", "Given a web search query, retrieve relevant passages that answer the query.")
        cap: dict = {}
        _patch_post(monkeypatch, 200, {"output": {"results": []}}, cap)

        rerank("年假多少天", ["docA", "docB", "docC"], top_n=2)

        body = cap["json"]
        assert body["model"] == "qwen3.7-text-rerank"
        assert body["input"]["query"] == "年假多少天"
        assert body["input"]["documents"] == ["docA", "docB", "docC"]
        assert body["parameters"]["top_n"] == 2
        assert "instruct" in body["parameters"]
        assert cap["headers"]["Authorization"] == "Bearer k"

    def test_gte_model_omits_instruct(self, monkeypatch):
        """gte-rerank 系列不支持 instruct（传了会 400）——按模型名决定是否带。"""
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        monkeypatch.setattr(settings, "RERANK_MODEL", "gte-rerank-v2")
        cap: dict = {}
        _patch_post(monkeypatch, 200, {"output": {"results": []}}, cap)

        rerank("q", ["doc"])

        assert "instruct" not in cap["json"]["parameters"]

    def test_empty_documents_returns_empty_without_call(self, monkeypatch):
        """空候选直接返回，不发请求（省一次 API 调用）。"""
        called = {"n": 0}

        def fake_post(self, *a, **kw):  # noqa: ANN001, ARG001
            called["n"] += 1
            return httpx.Response(200, json={"output": {"results": []}})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        assert rerank("q", []) == []
        assert called["n"] == 0


class TestRerankResponse:
    def test_parses_index_and_score(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        _patch_post(
            monkeypatch,
            200,
            {"output": {"results": [{"index": 2, "relevance_score": 0.93}, {"index": 0, "relevance_score": 0.17}]}},
        )

        results = rerank("q", ["a", "b", "c"])

        assert [(r.index, round(r.score, 2)) for r in results] == [(2, 0.93), (0, 0.17)]

    def test_sorted_descending_by_score(self, monkeypatch):
        """不依赖服务端排序保证，显式按分数降序。"""
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        _patch_post(
            monkeypatch,
            200,
            {"output": {"results": [{"index": 0, "relevance_score": 0.1}, {"index": 1, "relevance_score": 0.9}]}},
        )

        results = rerank("q", ["a", "b"])

        assert results[0].index == 1

    def test_malformed_items_skipped(self, monkeypatch):
        """字段异常的单项跳过，不让整批失败。"""
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        _patch_post(
            monkeypatch,
            200,
            {"output": {"results": [{"index": "x", "relevance_score": 0.5}, {"index": 1, "relevance_score": 0.6}]}},
        )

        results = rerank("q", ["a", "b"])

        assert len(results) == 1
        assert results[0].index == 1


class TestRerankErrors:
    def test_http_error_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        _patch_post(monkeypatch, 401, {"code": "InvalidApiKey", "message": "Invalid API-key provided."})
        with pytest.raises(RerankError):
            rerank("q", ["doc"])

    def test_business_error_code_raises(self, monkeypatch):
        """200 但带 code（百炼业务错误体）→ 仍须抛错。"""
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        _patch_post(monkeypatch, 200, {"code": "InvalidApiKey", "message": "bad key"})
        with pytest.raises(RerankError, match="InvalidApiKey"):
            rerank("q", ["doc"])

    def test_missing_output_results_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        _patch_post(monkeypatch, 200, {"output": {}})
        with pytest.raises(RerankError, match="结构异常"):
            rerank("q", ["doc"])

    def test_empty_query_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        with pytest.raises(RerankError):
            rerank("   ", ["doc"])


class TestGetRerankClient:
    def test_disabled_raises(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANK", False)
        with pytest.raises(ModelNotConfiguredError, match="RAG_ENABLE_RERANK"):
            get_rerank_client()

    def test_enabled_returns_callable(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANK", True)
        monkeypatch.setattr(settings, "RERANK_BASE_URL", "https://x.example.com")
        monkeypatch.setattr(settings, "RERANK_API_KEY", "k")
        assert callable(get_rerank_client())


class TestRerankPipelineNode:
    def _pipeline(self, chunks):
        p = Pipeline(query="q", kb_id=__import__("uuid").uuid4())
        p.chunks = chunks
        p.dense_scores = [c.dense_score for c in chunks]
        return p

    def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANK", False)
        chunks = [_FakeChunk("c1", "a", 0.9), _FakeChunk("c2", "b", 0.8)]
        p = self._pipeline(chunks)
        out = rerank_chunks(p)
        assert out.chunks == chunks
        assert out.stages == []

    def test_single_chunk_skips_api(self, monkeypatch):
        """候选 < 2 条时排序无意义，跳过调用。"""
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANK", True)
        called = {"n": 0}

        def fake_rerank(*a, **kw):  # noqa: ANN001, ARG001
            called["n"] += 1
            return []

        monkeypatch.setattr("app.services.steps.rerank.rerank", fake_rerank)
        p = self._pipeline([_FakeChunk("c1", "a", 0.9)])
        rerank_chunks(p)
        assert called["n"] == 0

    def test_reorders_and_preserves_dense_score(self, monkeypatch):
        """重排后顺序变、dense_score 原样保留（拒答语义不受重排影响）。"""
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANK", True)
        monkeypatch.setattr(settings, "RERANK_CANDIDATES", 24)
        monkeypatch.setattr(settings, "RERANK_TOP_N", 2)

        class _R:
            def __init__(self, i, s):
                self.index = i
                self.score = s

        monkeypatch.setattr(
            "app.services.steps.rerank.rerank",
            lambda *a, **kw: [_R(1, 0.9), _R(0, 0.4)],
        )
        chunks = [_FakeChunk("c1", "a", 0.91), _FakeChunk("c2", "b", 0.72)]
        p = self._pipeline(chunks)
        out = rerank_chunks(p)
        assert [c.chunk_id for c in out.chunks] == ["c2", "c1"]
        assert out.dense_scores == [0.72, 0.91]

    def test_fail_open_on_rerank_error(self, monkeypatch):
        """rerank 失败 → 保留原顺序（fail-open），不整条失败。"""
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANK", True)

        def boom(*a, **kw):  # noqa: ANN001, ARG001
            raise RerankError("boom")

        monkeypatch.setattr("app.services.steps.rerank.rerank", boom)
        chunks = [_FakeChunk("c1", "a", 0.9), _FakeChunk("c2", "b", 0.8)]
        p = self._pipeline(chunks)
        out = rerank_chunks(p)
        assert out.chunks == chunks
        assert any("rerank_degraded" in e for e in out.errors)
        assert any(s["name"] == "rerank" and s["error"] for s in out.stages)

    def test_empty_result_keeps_chunks(self, monkeypatch):
        """rerank 返回空 → 绝不把 chunks 清空。"""
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANK", True)
        monkeypatch.setattr("app.services.steps.rerank.rerank", lambda *a, **kw: [])
        chunks = [_FakeChunk("c1", "a", 0.9), _FakeChunk("c2", "b", 0.8)]
        p = self._pipeline(chunks)
        out = rerank_chunks(p)
        assert out.chunks == chunks
        assert "rerank_empty_result" in out.errors

    def test_candidates_capped(self, monkeypatch):
        """只送前 RERANK_CANDIDATES 条去重排（控成本/时延）。"""
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANK", True)
        monkeypatch.setattr(settings, "RERANK_CANDIDATES", 2)
        monkeypatch.setattr(settings, "RERANK_TOP_N", 2)
        seen: dict = {}

        class _R:
            def __init__(self, i, s):
                self.index = i
                self.score = s

        def fake(query, docs, **kw):  # noqa: ANN001
            seen["docs"] = docs
            return [_R(0, 0.9)]

        monkeypatch.setattr("app.services.steps.rerank.rerank", fake)
        chunks = [_FakeChunk(f"c{i}", f"t{i}", 0.5) for i in range(5)]
        p = self._pipeline(chunks)
        rerank_chunks(p)
        assert seen["docs"] == ["t0", "t1"]
