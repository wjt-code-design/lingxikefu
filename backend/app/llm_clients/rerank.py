"""Rerank 预留接口（MVP 关闭）。

决策（2026-08-15）：MVP 不做 rerank——小知识库 dense+sparse+RRF 已够，省成本与时延；
管线预留节点，M3 评测 recall@5 不达标再启用（RAG_ENABLE_RERANK=true）。
启用后实现：本地 cross-encoder（对话/评测模型已全面收敛 LongCat，不再走外部网关 rerank）。
"""
from __future__ import annotations

from app.core.config import settings
from app.llm_clients.base import ModelNotConfiguredError


def get_rerank_client():  # type: ignore[no-untyped-def]
    """返回 rerank client；未启用时抛可操作错误（提示怎么开）。"""
    if not settings.RAG_ENABLE_RERANK:
        raise ModelNotConfiguredError(
            "RAG_ENABLE_RERANK=false（默认）：MVP 不做 rerank。"
            "若评测 recall@5 不达标，设 RAG_ENABLE_RERANK=true 并实现 rerank client"
            f"（模型: {settings.RERANK_MODEL}）"
        )
    raise ModelNotConfiguredError(
        "RAG_ENABLE_RERANK=true 但 rerank client 尚未实现（预留接口，见 BU-05）"
    )
