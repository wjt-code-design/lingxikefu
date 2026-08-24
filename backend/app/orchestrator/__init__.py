"""RAG 管线编排核心：PipelineRunner —— 顺序执行 + 条件短路 + 节点重试。"""
from __future__ import annotations

import logging
from typing import Callable

from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)

#: 节点函数签名：输入 Pipeline，输出 Pipeline
NodeFn = Callable[[Pipeline], Pipeline]


class PipelineRunner:
    """轻量 RAG 管线调度器。

    与 LangGraph 的区别：
    - 当前只有 6 个节点、2 个条件分支，用 if/for 足够
    - 不需要图序列化、状态持久化、checkpoint 恢复
    - 未来节点 > 20 时再迁移到 LangGraph
    """

    def __init__(self, retry: int = 1) -> None:
        """初始化 Runner。

        Args:
            retry: 节点级重试次数（默认 1 次，即失败后重试一次）
        """
        self.retry = retry

    def run(self, pipeline: Pipeline, nodes: list[NodeFn]) -> Pipeline:
        """顺序执行节点列表，支持条件短路和节点重试。

        短路规则：
        - intent != "qa" 时跳过 rewrite/cache/retrieve/refuse
        - from_cache = True 时跳过 retrieve/refuse

        Args:
            pipeline: 管线上下文（承载中间状态）
            nodes: 节点函数列表（按执行顺序）

        Returns:
            执行完毕的 Pipeline 上下文
        """
        for node in nodes:
            name = getattr(node, "__name__", str(node))

            # 条件短路：非 qa 意图跳过后续检索节点
            if pipeline.intent and pipeline.intent != "qa":
                logger.debug("Runner: 意图=%s, 跳过节点 %s", pipeline.intent, name)
                continue

            # 条件短路：缓存命中跳过检索节点
            if pipeline.from_cache and name in ("rewrite_query", "check_cache", "retrieve_chunks", "check_refuse"):
                logger.debug("Runner: 缓存命中, 跳过节点 %s", name)
                continue

            # 节点级重试
            last_error: Exception | None = None
            for attempt in range(self.retry + 1):
                try:
                    pipeline = node(pipeline)
                    break  # 成功 → 跳出重试循环
                except Exception as e:
                    last_error = e
                    if attempt < self.retry:
                        logger.warning(
                            "Runner: 节点 %s 第 %d 次失败, 重试: %s",
                            name, attempt + 1, e,
                        )
                    else:
                        logger.error(
                            "Runner: 节点 %s 重试 %d 次后仍失败: %s",
                            name, self.retry, e,
                        )
                        pipeline.errors.append(f"[{name}] {e}")
                        raise

        return pipeline
