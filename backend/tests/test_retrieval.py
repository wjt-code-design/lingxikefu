"""检索侧测试（BU-05）：参数校验 / query 前缀 / Qdrant search 调用 / 失败降级。

- mock embedding 与 qdrant：不依赖真实 bge 与 Qdrant server；
- 验证 query 加了 BGE_QUERY_PREFIX（检索质量的关键事实，防漏）。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from app.llm_clients.embedding import BGE_QUERY_PREFIX
from app.services.retrieval_service import RetrievalError, RetrievedChunk, search_kb
from app.services.vector_service import VectorStoreError


class FakeEmbedding:
    dim = 768

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[0.1] * self.dim for _ in texts]


class FakeHit:
    def __init__(self, score, payload, pid=None):
        self.id = pid or payload.get("chunk_id", "p")
        self.score = score
        self.payload = payload


class FakeQdrant:
    def __init__(self):
        self.search_calls: list[dict] = []

    def search(self, collection_name, query_vector, limit, query_filter):
        self.search_calls.append(
            {
                "collection_name": collection_name,
                "query_vector": query_vector,
                "limit": limit,
                "query_filter": query_filter,
            }
        )
        return [
            FakeHit(
                0.9,
                {
                    "chunk_id": "c1",
                    "doc_id": "d1",
                    "kb_id": "kb1",
                    "idx": 0,
                    "text": "退款政策第一条",
                },
                pid="c1",
            ),
            FakeHit(0.8, {"chunk_id": "c2", "doc_id": "d1", "kb_id": "kb1", "idx": 1, "text": "退款政策第二条"}, pid="c2"),
        ]


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    emb = FakeEmbedding()
    qd = FakeQdrant()
    monkeypatch.setattr(
        "app.services.retrieval_service.get_embedding_client", lambda: emb
    )
    monkeypatch.setattr("app.services.retrieval_service.get_qdrant_client", lambda: qd)
    monkeypatch.setattr(
        "app.services.retrieval_service.settings.TENANT_DEFAULT", "default", raising=False
    )
    monkeypatch.setattr(
        "app.services.retrieval_service.settings.QDRANT_COLLECTION",
        "lingxi_bge_768",
        raising=False,
    )
    return {"emb": emb, "qd": qd}


def test_search_kb_query_has_bge_prefix(patch):
    """核心事实：query 必须加 BGE_QUERY_PREFIX，否则与入库 document 语义空间不匹配。"""
    kb_id = uuid4()
    hits = search_kb("退款多久到账", kb_id, top_k=5)

    sent = patch["emb"].calls[0][0]
    assert sent == BGE_QUERY_PREFIX + "退款多久到账"
    assert len(hits) == 2
    assert isinstance(hits[0], RetrievedChunk)
    assert hits[0].doc_id == "d1" and hits[0].text == "退款政策第一条"


def test_search_kb_filter_tenant_and_kb(patch):
    """检索必须按 tenant_id + kb_id 过滤（多租户隔离，单租户 MVP 双保险）。"""
    kb_id = uuid4()
    search_kb("退货", kb_id)

    flt = patch["qd"].search_calls[0]["query_filter"]
    keys = {c.key for c in flt.must}
    # G1 门禁 v2：visible=True 加入 must（staged 未发布 chunk 对检索不可见）
    assert keys == {"tenant_id", "kb_id", "visible"}
    visible_cond = next(c for c in flt.must if c.key == "visible")
    assert visible_cond.match.value is True
    # kb_id 匹配值必须是我们传的那个
    kb_cond = next(c for c in flt.must if c.key == "kb_id")
    assert kb_cond.match.value == str(kb_id)


def test_search_kb_top_k_passthrough(patch):
    """hybrid：双路各取候选池（_HYBRID_CANDIDATES），RRF 融合后返回 top_k。"""
    hits = search_kb("发票", uuid4(), top_k=3)
    assert patch["qd"].search_calls[0]["limit"] == 24  # dense 候选池
    assert len(patch["qd"].search_calls) == 2  # dense + sparse 双路
    assert len(hits) <= 3


def test_search_kb_empty_query_rejected(patch):
    with pytest.raises(RetrievalError, match="query 为空"):
        search_kb("   ", uuid4())


def test_search_kb_bad_top_k_rejected(patch):
    with pytest.raises(RetrievalError, match="top_k"):
        search_kb("退货", uuid4(), top_k=0)


def test_search_kb_qdrant_failure_raises(patch, monkeypatch):
    """fail-closed：Qdrant 不可达必须抛 RetrievalError，不静默返回空，且不泄漏内部 QDRANT_URL。"""
    from app.core.config import settings

    def boom(*_a, **_k):
        raise VectorStoreError("Qdrant 不可达")

    monkeypatch.setattr(
        "app.services.retrieval_service.get_qdrant_client", boom
    )
    with pytest.raises(RetrievalError, match="vector store unavailable") as excinfo:
        search_kb("退货", uuid4())
    # P2-④：异常 message 不得拼接 QDRANT_URL（内部端点信息不外泄）
    assert settings.QDRANT_URL not in str(excinfo.value)
