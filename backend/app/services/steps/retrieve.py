"""混合检索节点：dense + sparse + RRF。"""
from __future__ import annotations

from app.services.pipeline import Pipeline


def retrieve_chunks(pipeline: Pipeline) -> Pipeline:
    """hybrid 检索：dense + sparse + RRF"""
    from app.services.retrieval_service import search_kb
    from app.core.config import settings

    chunks = search_kb(
        pipeline.rewritten_query, pipeline.kb_id, top_k=settings.RETRIEVAL_TOP_K
    )
    pipeline.chunks = chunks
    pipeline.dense_scores = [c.dense_score for c in chunks]
    pipeline.add_stage("retrieve")
    return pipeline
