"""查询改写节点：同义归一 + 方言 + 语气词 + 指代消解。"""
from __future__ import annotations

from app.services.pipeline import Pipeline


def rewrite_query(pipeline: Pipeline) -> Pipeline:
    """查询改写：T9-S3 改写只服务检索与缓存 key"""
    from app.services.query_rewrite import rewrite

    rewritten, _ = rewrite(pipeline.query, pipeline.history)
    pipeline.rewritten_query = rewritten
    pipeline.add_stage("rewrite")
    return pipeline
