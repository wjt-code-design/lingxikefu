"""拒答判定节点：诚实性拒答 + 降噪过滤。"""
from __future__ import annotations

from app.services.pipeline import Pipeline


def check_refuse(pipeline: Pipeline) -> Pipeline:
    """诚实性拒答：best_dense < MIN_SCORE → refuse + 降噪过滤"""
    from app.core.config import settings

    best_dense = max(pipeline.dense_scores, default=0.0)
    if not pipeline.chunks or best_dense < settings.MIN_SCORE:
        pipeline.refuse = True
        pipeline.refuse_reason = "未找到可靠依据"
    # 降噪：低分近义片段不进 prompt
    pipeline.chunks = [
        c for c in pipeline.chunks if c.dense_score >= settings.MIN_SCORE
    ]
    pipeline.add_stage("refuse_check")
    return pipeline
