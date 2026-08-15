"""检索侧（BU-05）：dense 检索，query 加 bge 指令前缀。

- ``search_kb``：Qdrant 按 kb_id + tenant_id 过滤的向量检索，返回 top_k 切片。
- query 统一加 BGE_QUERY_PREFIX（官方推荐，document 入库时未加，语义空间一致）。
- 深接口薄实现：调用方只传 (query, kb_id)，不感知 Qdrant 细节。
- 所有失败抛 ``RetrievalError``（fail-closed）：检索不可用绝不静默返回空。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from app.core.config import settings
from app.llm_clients.embedding import BGE_QUERY_PREFIX, get_embedding_client
from app.services.vector_service import get_qdrant_client

logger = logging.getLogger(__name__)


class RetrievalError(Exception):
    """检索失败（调用方应显式降级，不静默吞掉）。"""


@dataclass
class RetrievedChunk:
    """检索命中的切片（Qdrant payload + 距离）。"""

    chunk_id: str
    doc_id: str
    kb_id: str
    idx: int
    text: str
    score: float


def search_kb(query: str, kb_id: UUID, top_k: int = 5) -> list[RetrievedChunk]:
    """dense 检索：query 加指令前缀后 embedding，Qdrant 按 kb 过滤取 top_k。"""
    if not query.strip():
        raise RetrievalError("检索 query 为空")
    if top_k < 1:
        raise RetrievalError(f"top_k 非法值: {top_k!r}（应 >= 1）")

    try:
        # 1) query embedding（中文 bge 需加指令前缀，与 document 侧区分）
        client = get_embedding_client()
        vector = client.embed([BGE_QUERY_PREFIX + query])[0]

        # 2) Qdrant search：按 tenant_id + kb_id 过滤（单租户 MVP 双保险）
        #    注意：qdrant-client 版本必须与 server 匹配（1.9.x 用 .search() 旧 API；
        #    server 1.9.1 不支持新 /points/query → client 高于 1.10 会 404）
        qdrant = get_qdrant_client()
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        hits = qdrant.search(
            collection_name=settings.QDRANT_COLLECTION,
            query_vector=vector,
            limit=top_k,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="tenant_id", match=MatchValue(value=settings.TENANT_DEFAULT)
                    ),
                    FieldCondition(key="kb_id", match=MatchValue(value=str(kb_id))),
                ]
            ),
        )
    except Exception as e:  # noqa: BLE001
        raise RetrievalError(f"dense 检索失败（{settings.QDRANT_URL}）: {e}") from e

    chunks: list[RetrievedChunk] = []
    for h in hits:
        p = h.payload or {}
        chunks.append(
            RetrievedChunk(
                chunk_id=str(p.get("chunk_id", "")),
                doc_id=str(p.get("doc_id", "")),
                kb_id=str(p.get("kb_id", "")),
                idx=int(p.get("idx", 0)),
                text=str(p.get("text", "")),
                score=float(h.score),
            )
        )
    return chunks
