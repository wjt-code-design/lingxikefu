"""意图分类节点：规则式，关键词匹配。"""
from __future__ import annotations

from app.services.pipeline import Pipeline


def classify_intent(pipeline: Pipeline) -> Pipeline:
    """规则式意图分类：handoff(人工+情绪) > chitchat > qa"""
    from app.services.rag_service import classify_intent as _classify

    pipeline.intent = _classify(pipeline.query)
    pipeline.add_stage("intent")
    return pipeline
