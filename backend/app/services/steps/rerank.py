"""rerank 节点：对混合召回结果重排（2026-10-27 接入百炼原生 rerank）。

位置契约：**retrieve 之后、refuse 之前**。
- 必须在 retrieve 之后：rerank 重排的是召回候选，需要先有候选。
- 必须在 refuse 之前：拒答判定读 ``pipeline.dense_scores``（稠密原始余弦分），
  而 rerank **不改变 dense_score**（见下），故拒答语义不受重排影响——
  这正是"排序分与相关性分解耦"设计的延续：rerank 只动"谁排前面"，不动"覆不覆盖"。

⚠️ dense_score 的保留：rerank 返回的 relevance_score 是**本次请求内的相对分**，
无绝对语义、不可跨请求比较，更不能当拒答阈值用（拒答仍走稠密余弦分）。
因此重排后按原 index 回填原始 chunk，**dense_score 原样保留**。

⚠️ fail-open：rerank 不可用（网络/限流/余额）时**降级为不重排**（保留原 RRF 序），
而不是整条问答失败——重排是效果增强项，不该成为可用性单点。降级会记入
``pipeline.errors`` 与阶段日志（可观测，不静默）。

同步性：管线编排器 ``PipelineRunner.run`` 是同步的（节点签名 ``(Pipeline) -> Pipeline``），
故本节点用同步 httpx 客户端（见 llm_clients/rerank_bailian.py 的说明）。
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.llm_clients.base import ModelNotConfiguredError
from app.llm_clients.rerank_bailian import RerankError, rerank
from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)


def rerank_chunks(pipeline: Pipeline) -> Pipeline:
    """对 ``pipeline.chunks`` 重排，原地替换为 top_n 条。

    未启用（``RAG_ENABLE_RERANK=false``）→ 直接返回，不做任何事。
    失败 → 保留原顺序（fail-open），并把原因记入 errors。
    """
    if not settings.RAG_ENABLE_RERANK:
        return pipeline

    chunks = pipeline.chunks
    # 候选不足 2 条时重排无意义（排序结果必然不变），直接跳过省一次 API 调用
    if len(chunks) < 2:
        pipeline.add_stage("rerank")
        return pipeline

    # 候选数上限：只把前 RERANK_CANDIDATES 条送去重排（控成本 + 控 token）
    candidates = chunks[: settings.RERANK_CANDIDATES]
    documents = [c.text for c in candidates]

    try:
        results = rerank(
            pipeline.rewritten_query or pipeline.query,
            documents,
            top_n=min(settings.RERANK_TOP_N, len(candidates)),
        )
    except (RerankError, ModelNotConfiguredError) as e:
        # fail-open：保留原 RRF 顺序，只记错误
        logger.warning("rerank 失败，降级为不重排（保留 RRF 序）: %s", e)
        pipeline.errors.append(f"rerank_degraded: {e}")
        pipeline.add_stage("rerank", error=str(e))
        return pipeline

    # 回填：按 rerank 返回的原始 index 取 chunk（保留原 dense_score / chunk_id / doc_id）
    reranked: list = []
    for r in results:
        if 0 <= r.index < len(candidates):
            reranked.append(candidates[r.index])

    # 防御：rerank 返回条数异常（空/越界）时保留原顺序，绝不把 chunks 清空
    if not reranked:
        logger.warning("rerank 返回空结果，保留原 RRF 序")
        pipeline.errors.append("rerank_empty_result")
        pipeline.add_stage("rerank", error="empty result")
        return pipeline

    pipeline.chunks = reranked
    # dense_scores 与 chunks 一一对应：重排后按新顺序重排分数
    # （dense_scores 是"稠密原始分"的有序列表，与 chunks 同序；重排后同步）
    dense_by_chunk = {c.chunk_id: c.dense_score for c in chunks}
    pipeline.dense_scores = [dense_by_chunk.get(c.chunk_id, 0.0) for c in reranked]
    pipeline.add_stage("rerank")
    return pipeline
