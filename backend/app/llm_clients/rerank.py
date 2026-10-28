"""Rerank 客户端入口（2026-10-27 接入百炼原生 rerank）。

沿革：
- 2026-08-15：MVP 决策不做 rerank（dense+sparse+RRF 对小知识库已够，省成本与时延），
  本模块只保留一个"抛错并告诉用户怎么开"的占位实现。
- 2026-10-27：**已实现**。启用百炼 qwen3.7-text-rerank（DashScope 原生端点），
  实际调用在 ``app/llm_clients/rerank_bailian.py``。

为什么用外部 API rerank 而非当初设想的本地 cross-encoder：
百炼的 qwen3.7-text-rerank 是专门训练的排序模型（支持 instruct 区分任务类型：
问答检索 vs 语义相似度），单文档 30K token / 单次 500 文档，能力与工程成熟度
都优于通用 cross-encoder；且**零本地部署成本**——这与项目「embedding 本地、
对话走 API」的成本模型一致。代价是每次检索多一次外部调用（已用
RERANK_CANDIDATES=24 控候选数以控 token 与时延）。

⚠️ 关键约束（务必先读）：百炼 rerank **不走 OpenAI 兼容端点**。打
``{base}/compatible-mode/v1/rerank`` 返回 HTTP 404 + 空响应体（无错误说明，
最难排查的一类故障）。必须走原生路径
``{base}/api/v1/services/rerank/text-rerank/text-rerank``。
"""
from __future__ import annotations

from app.core.config import settings
from app.llm_clients.base import ModelNotConfiguredError
from app.llm_clients.rerank_bailian import RerankError, RerankResult, rerank

__all__ = ["RerankError", "RerankResult", "get_rerank_client", "rerank"]


def get_rerank_client():  # type: ignore[no-untyped-def]
    """返回 rerank 客户端工厂（校验开关与配置）。

    Raises:
        ModelNotConfiguredError: 开关关闭、或缺端点/Key 时抛可操作错误。
    """
    if not settings.RAG_ENABLE_RERANK:
        raise ModelNotConfiguredError(
            "RAG_ENABLE_RERANK=false（默认）：不做 rerank。"
            "若评测 recall@5 不达标，设 RAG_ENABLE_RERANK=true 并配置 RERANK_BASE_URL/RERANK_API_KEY"
            f"（模型: {settings.RERANK_MODEL}）"
        )
    if not (settings.RERANK_BASE_URL or "").strip():
        raise ModelNotConfiguredError(
            "RAG_ENABLE_RERANK=true 但 RERANK_BASE_URL 未配置"
            "（百炼 rerank 需原生端点，如 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com，"
            "勿带 /compatible-mode/v1）"
        )
    if not (settings.RERANK_API_KEY or "").strip():
        raise ModelNotConfiguredError(
            "RAG_ENABLE_RERANK=true 但 RERANK_API_KEY 未配置"
        )
    return rerank
