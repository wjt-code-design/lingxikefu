"""缓存命中节点：精确 + 语义双命中。"""
from __future__ import annotations

from app.services.pipeline import Pipeline


def check_cache(pipeline: Pipeline) -> Pipeline:
    """缓存命中（精确+语义，实体锁定+KB 版本校验）→ 不走检索/LLM"""
    from app.services.answer_cache import get as cache_get

    cached = cache_get(
        # B8（审计 M4）：None 必须原样传下去（= 不过滤）；str(None) 会写出字面串 "None"
        # → 缓存键 ...:None:<sha> + Qdrant 过滤 kb_id=="None" → 语义层恒 miss（缓存静默失效）
        pipeline.rewritten_query,
        pipeline.kb_version,
        kb_id=str(pipeline.kb_id) if pipeline.kb_id else None,
    )
    if cached:
        pipeline.from_cache = True
        pipeline.cached_answer = cached.get("answer", "")
        pipeline.cached_sources = cached.get("sources", [])
    pipeline.add_stage("cache_check")
    return pipeline
