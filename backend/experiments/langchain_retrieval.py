"""LangChain 版检索层（2026-10-27 · A/B 对比实验用）。

目的：用 **LangChain 官方组件** 复现灵犀的混合检索，与自研版在同一评测集上对比。
自研版见 ``app/services/retrieval_service.py``（dense + sparse + 加权 RRF）。

⚠️ 三条必须保持一致的口径（否则 A/B 无意义）：
1. **同一 Qdrant 集合**（lingxi_hybrid_bge_768）——只读，不新建、不写入
2. **同一过滤条件**（tenant_id / kb_id / visible=True）
3. **同一候选池**（自研 sparse 候选 24）——BM25 的 k 与 dense 候选数都对齐到它

⚠️ 为什么不用 langchain-qdrant：它要求 ``qdrant-client>=1.10.1``，而本项目
生产 server 是 **qdrant v1.9.1**，client >1.10 会走 ``/points/query`` 新 API
导致检索 404（requirements.txt 有明确锁定与注释）。故 dense 侧直接用已有的
``qdrant_client`` 原生调用——这正是 LangChain 官方允许的接法（Retriever 可插拔）。

🔑 实测到的框架能力（langchain 0.3.30 / langchain-core 0.3.86）：
``EnsembleRetriever(retrievers=[...], weights=[...], c=60, id_key=...)``
- **支持 weights**（默认等权）→ 可与自研的「加权 RRF 2:1」直接对照，
  把「融合权重」这一个变量单独隔离出来
- c 默认 60（与自研 ``_RRF_K = 60`` 一致）
- ``id_key`` 默认用 page_content 去重——本项目须显式改为 chunk_id，
  否则同一文档的不同 chunk 会被误判为同一篇而丢掉

⚠️ 与自研版的**已知混淆因子**（必须如实记录，不可隐瞒）：
- **sparse 算法不同**：自研 = 字符级 2-gram + crc32 哈希 + 词频 tf（无 IDF）；
  LangChain BM25 = jieba 分词 + 真正的 BM25（含 IDF）。这是系统级差异。
- 因此结论表述为「**系统级对比**」，不宣称单变量因果。
  额外用「自研等权 RRF」臂（``_rrf_fuse`` 支持传参）隔离出「权重」的贡献。

⚠️ dense_score 回填：EnsembleRetriever 融合后丢失原始分，拒答阈值需要 dense
原始余弦分 → 由 dense 侧单独建 chunk_id→score 映射回填（语义与自研一致）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import settings
from app.llm_clients.embedding import BGE_QUERY_PREFIX, get_embedding_client
from app.services.retrieval_service import RetrievedChunk

logger = logging.getLogger(__name__)


@dataclass
class LangChainRetrievalResult:
    """LangChain 臂的检索结果（与自研 RetrievedChunk 对齐）。"""

    chunks: list[RetrievedChunk]
    dense_score_map: dict[str, float]


def _qdrant_client():
    from qdrant_client import QdrantClient

    return QdrantClient(url=settings.QDRANT_URL)


def _filter(kb_id):
    """与自研 search_kb 完全一致的过滤条件（三条 must）。"""
    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    return Filter(
        must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=settings.TENANT_DEFAULT)),
            FieldCondition(key="kb_id", match=MatchValue(value=str(kb_id))),
            FieldCondition(key="visible", match=MatchValue(value=True)),
        ]
    )


def _dense_hits(query: str, kb_id, limit: int, collection: str):
    """原生 dense 检索 → [(point_id, cosine_score, payload)]。

    与自研一致：dense 侧加 BGE_QUERY_PREFIX（sparse 侧用原文不加前缀）。
    """
    client = _qdrant_client()
    embed_client = get_embedding_client()
    dense_vec = embed_client.embed([BGE_QUERY_PREFIX + query])[0]
    # ⚠️ qdrant-client **1.9.x** 用 ``client.search(...)``；``query_points`` 是 1.10+ 的 API。
    # 本项目 server 固定 v1.9.1、client 锁 <1.10（requirements.txt 有注释），
    # 故必须用 search()——与自研 retrieval_service 的写法保持一致。
    hits = client.search(
        collection_name=collection,
        query_vector=("dense", dense_vec),
        limit=limit,
        query_filter=_filter(kb_id),
        with_payload=True,
    )
    return [(str(h.id), float(h.score), (h.payload or {})) for h in hits]


def _precomputed(docs: list):
    """把已算好的命中包装成 LangChain 的 **Runnable**。

    ⚠️ EnsembleRetriever 的 retrievers 字段经 pydantic 校验，要求元素是
    ``Runnable`` 实例（不是任意带 get_relevant_documents 的对象——
    那样会在构造时直接 ValidationError）。
    故用 ``RunnableLambda`` 包装——这正是 LangChain 官方的可插拔扩展方式。

    LangChain 的 Retriever/Runnable 可插拔是它的核心设计点：本项目 dense 侧
    必须用原生 qdrant_client（server 锁 v1.9.1，client >1.10 会 404），
    以适配器接入而非强行迁到 langchain-qdrant。
    """
    from langchain_core.runnables import RunnableLambda

    return RunnableLambda(lambda _query: list(docs), name="precomputed")


def langchain_search_kb(
    query: str,
    kb_id,
    top_k: int = 8,
    *,
    weights: list[float] | None = None,
) -> list[RetrievedChunk]:
    """LangChain 版混合检索（签名与自研 search_kb 对齐，可直接替换对比）。

    Args:
        query: 查询（已改写）。
        kb_id: 知识库 ID。
        top_k: 返回条数。
        weights: EnsembleRetriever 权重（None=等权；[2.0,1.0]=与自研加权一致）。

    Returns:
        RetrievedChunk 列表，dense_score 已回填（拒答阈值语义与自研一致）。
    """
    from langchain.retrievers import EnsembleRetriever
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document

    # ⚠️ 用模块级捕获的原始函数：函数体内的 import 会取到 A/B 实验 patch 后的值
    #    → 自我递归（实测 RecursionError）。原始函数的捕获见本模块末尾。
    collection = settings.QDRANT_COLLECTION_HYBRID
    candidates = settings.RERANK_CANDIDATES  # 与自研 sparse 候选池一致（24）

    # 1) 候选集：用自研检索取（保证两臂面对**完全同一批候选**，只让融合方式不同）
    native_chunks = _ORIGINAL_SEARCH_KB(query, kb_id, top_k=candidates)
    if len(native_chunks) < 2:
        return native_chunks[:top_k]
    cand_texts = [c.text for c in native_chunks]
    cand_meta = [
        {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "idx": c.idx,
            "dense_score": c.dense_score,
        }
        for c in native_chunks
    ]

    # 2) dense 侧：原生检索（同一集合同一过滤），候选数对齐
    dense_hits = _dense_hits(query, kb_id, candidates, collection)
    dense_map = {pid: score for pid, score, _ in dense_hits}
    dense_docs = [
        Document(
            page_content=p.get("text", ""),
            metadata={**p, "_score": s, "_pid": pid},
        )
        for pid, s, p in dense_hits
    ]

    # 3) sparse 侧：LangChain BM25（jieba 分词 + IDF，与自研 2-gram tf 不同——
    #    这是刻意保留的混淆因子，用于系统级对比）
    # ⚠️ from_documents 要 Document 对象（不是 dict）——langchain-community 的接口约定
    bm25 = BM25Retriever.from_documents(
        [Document(page_content=t, metadata=m) for t, m in zip(cand_texts, cand_meta)],
        k=len(cand_texts),
    )
    sparse_docs = bm25.invoke(query)

    # 4) LangChain 官方 RRF 融合（c=60；weights=None 等权）
    ens = EnsembleRetriever(
        retrievers=[_precomputed(dense_docs), _precomputed(sparse_docs)],
        weights=weights,
        c=60,
        id_key="chunk_id",   # ⚠️ 必须显式指定：默认按 page_content 去重会丢 chunk
    )
    fused = ens.invoke(query)

    # 5) 回填 dense_score（融合后丢了原始分，拒答阈值要用）
    out: list[RetrievedChunk] = []
    for doc in fused[:top_k]:
        meta = doc.metadata or {}
        dense_score = dense_map.get(meta.get("_pid"), meta.get("dense_score", 0.0))
        out.append(
            RetrievedChunk(
                chunk_id=meta.get("chunk_id") or meta.get("_pid", ""),
                doc_id=meta.get("doc_id", ""),
                kb_id=str(kb_id),
                idx=meta.get("idx", 0),
                text=doc.page_content,
                score=float(meta.get("_score", 0.0)),
                dense_score=float(dense_score),
            )
        )
    return out


# 🔑 模块级捕获原始 search_kb —— A/B 实验会 patch 源模块的 search_kb，
#    函数体内的 import 会取到 patch 后的值 → 自我递归。故此处提前捕获原函数。
from app.services.retrieval_service import search_kb as _ORIGINAL_SEARCH_KB  # noqa: E402
