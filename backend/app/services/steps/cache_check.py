"""缓存命中节点：精确 + 语义双命中。"""
from __future__ import annotations

from app.services.pipeline import Pipeline


def check_cache(pipeline: Pipeline) -> Pipeline:
    """缓存命中（精确+语义，实体锁定+KB 版本校验）→ 不走检索/LLM"""
    from app.services.answer_cache import get as cache_get

    cached = cache_get(
        pipeline.rewritten_query, pipeline.kb_version, kb_id=str(pipeline.kb_id)
    )
    if cached:
        pipeline.from_cache = True
        pipeline.cached_answer = cached.get("answer", "")
        pipeline.cached_sources = cached.get("sources", [])
    pipeline.add_stage("cache_check")
    return pipeline
