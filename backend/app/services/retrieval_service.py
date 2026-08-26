"""检索侧（BU-05）：hybrid 检索（dense + sparse + RRF）。

- ``search_kb``：Qdrant 按 kb_id + tenant_id 过滤的向量检索，返回 top_k 切片。
- **hybrid（ADR-2026-08-16）**：dense（bge 语义）+ sparse（bigram BM25 词面）双路检索，
  RRF（k=60）融合排序；chunk 保留 ``dense_score``（dense 原始余弦分数）供拒答判定解耦
  （排序看 RRF，相关性看 dense——RRF 分数无绝对语义，不能直接做阈值）。
- query 的 sparse 用原文（不加 BGE_QUERY_PREFIX：前缀是给 dense 的检索指令，词面匹配会污染）。
- 所有失败抛 ``RetrievalError``（fail-closed）：检索不可用绝不静默返回空。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from qdrant_client.http.models import FieldCondition, Filter, MatchValue, NamedSparseVector

from app.core.config import settings
from app.llm_clients.embedding import BGE_QUERY_PREFIX, get_embedding_client
from app.services.sparse_util import text_to_sparse
from app.services.vector_service import get_collection_name, get_qdrant_client

logger = logging.getLogger(__name__)

#: RRF 融合常数（标准值 60）；sparse/dense 各自取 top_k*3 候选进融合
_RRF_K = 60
_HYBRID_CANDIDATES = 24


class RetrievalError(Exception):
    """检索失败（调用方应显式降级，不静默吞掉）。"""


@dataclass
class RetrievedChunk:
    """检索命中的切片（Qdrant payload + 分数）。

    score: RRF 融合分（hybrid）/ 余弦相似度（纯 dense）。
    dense_score: dense 原始余弦分数（hybrid 下供拒答判定；纯 dense 下与 score 相同）。
    """

    chunk_id: str
    doc_id: str
    kb_id: str
    idx: int
    text: str
    score: float
    dense_score: float = field(default=0.0)


def _to_chunk(payload: dict, score: float, dense_score: float) -> RetrievedChunk:
    p = payload or {}
    return RetrievedChunk(
        chunk_id=str(p.get("chunk_id", "")),
        doc_id=str(p.get("doc_id", "")),
        kb_id=str(p.get("kb_id", "")),
        idx=int(p.get("idx", 0)),
        text=str(p.get("text", "")),
        score=score,
        dense_score=dense_score,
    )


def _rrf_fuse(dense_hits, sparse_hits, top_k: int, w_dense: float = 2.0, w_sparse: float = 1.0) -> list[tuple[str, float, dict]]:
    """加权 RRF 融合：score = Σ w/(k+rank)，返回 [(point_id, rrf_score, payload)] 取 top_k。

    dense 权重更高（w_dense=2.0）：dense 是主检索（语义），sparse 是词面补充。
    实测教训（Q072 诈骗短信→隐私政策）：纯 rank RRF 让"双路命中"的噪声 chunk 总分
    压过"dense 强命中但 sparse 未命中"的真答案（隐私政策 dense top1 被挤出 top8）。
    加权后强 dense 命中不被 sparse 噪声淹没（ADR-2026-08-16 §3.4 修正）。
    """
    acc: dict[str, float] = {}
    payloads: dict[str, dict] = {}
    for hits, w in ((dense_hits, w_dense), (sparse_hits, w_sparse)):
        for rank, h in enumerate(hits):
            pid = str(h.id)
            acc[pid] = acc.get(pid, 0.0) + w / (_RRF_K + rank + 1)
            payloads.setdefault(pid, h.payload or {})
    ranked = sorted(acc.items(), key=lambda x: -x[1])[:top_k]
    return [(pid, score, payloads[pid]) for pid, score in ranked]


def _dense_score_map(dense_hits) -> dict[str, float]:
    return {str(h.id): float(h.score) for h in dense_hits}


def search_kb(query: str, kb_id: UUID, top_k: int = 8) -> list[RetrievedChunk]:
    """hybrid 检索：dense + sparse 双路 → RRF 融合；非 hybrid 走纯 dense。"""
    if not query.strip():
        raise RetrievalError("检索 query 为空")
    if top_k < 1:
        raise RetrievalError(f"top_k 非法值: {top_k!r}（应 >= 1）")

    try:
        client = get_embedding_client()
        dense_vec = client.embed([BGE_QUERY_PREFIX + query])[0]
        qdrant = get_qdrant_client()
        qfilter = Filter(
            must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=settings.TENANT_DEFAULT)),
                FieldCondition(key="kb_id", match=MatchValue(value=str(kb_id))),
            ]
        )
        name = get_collection_name()

        if settings.RAG_ENABLE_HYBRID:
            dense_hits = qdrant.search(
                collection_name=name,
                query_vector=("dense", dense_vec),
                limit=_HYBRID_CANDIDATES,
                query_filter=qfilter,
            )
            sparse_vec = text_to_sparse(query)  # sparse 用原文，不加 BGE 前缀
            sparse_hits = qdrant.search(
                collection_name=name,
                query_vector=NamedSparseVector(name="sparse", vector=sparse_vec),
                limit=_HYBRID_CANDIDATES,
                query_filter=qfilter,
            )
            dense_scores = _dense_score_map(dense_hits)
            fused = _rrf_fuse(dense_hits, sparse_hits, top_k)
            chunks = [_to_chunk(payload, score, dense_scores.get(pid, 0.0)) for pid, score, payload in fused]
        else:
            hits = qdrant.search(
                collection_name=name,
                query_vector=dense_vec,
                limit=top_k,
                query_filter=qfilter,
            )
            chunks = [_to_chunk(h.payload, float(h.score), float(h.score)) for h in hits]
    except Exception as e:  # noqa: BLE001
        # 不泄漏内部 QDRANT_URL 到对外 message；详情走日志（P2-④）
        logger.exception("dense 检索失败（qdrant url=%s）", settings.QDRANT_URL)
        raise RetrievalError("vector store unavailable") from e

    return chunks
