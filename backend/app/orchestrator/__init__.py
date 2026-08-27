"""RAG 管线编排核心：PipelineRunner —— 顺序执行 + 条件短路 + 节点重试 + 时间预算。"""
from __future__ import annotations

import logging
import time
from typing import Callable

from app.services.pipeline import Pipeline

logger = logging.getLogger(__name__)

#: 节点函数签名：输入 Pipeline，输出 Pipeline
NodeFn = Callable[[Pipeline], Pipeline]


class PipelineTimeoutError(TimeoutError):
    """管线整体时间预算用尽（P2-3 对抗审查 2026-08-27）。

    编排层防"节点挂起导致请求吊死"：预算在节点之间检查（无法中断单个阻塞节点），
    超时由调用方按降级语义处理（run_pipeline 将其与检索失败同路降级为诚实拒答）。
    """


class PipelineRunner:
    """轻量 RAG 管线调度器。

    与 LangGraph 的区别：
    - 当前只有 6 个节点、2 个条件分支，用 if/for 足够
    - 不需要图序列化、状态持久化、checkpoint 恢复
    - 未来节点 > 20 时再迁移到 LangGraph
    """

    def __init__(self, retry: int = 1, time_budget: float = 20.0) -> None:
        """初始化 Runner。

        Args:
            retry: 节点级重试次数（默认 1 次，即失败后重试一次）
            time_budget: 整体时间预算（秒）。预算在节点之间检查，
                超限抛 PipelineTimeoutError（诚实标注：同步节点无法被中断，
                长时间卡在单个节点内的极端情况仍需依赖下游 HTTP 超时兜底）。
        """
        self.retry = retry
        self.time_budget = time_budget

    def run(self, pipeline: Pipeline, nodes: list[NodeFn]) -> Pipeline:
        """顺序执行节点列表，支持条件短路、节点重试与整体时间预算。

        短路规则：
        - intent != "qa" 时跳过 rewrite/cache/retrieve/refuse
        - from_cache = True 时跳过 retrieve/refuse

        Args:
            pipeline: 管线上下文（承载中间状态）
            nodes: 节点函数列表（按执行顺序）

        Returns:
            执行完毕的 Pipeline 上下文
        """
        start = time.monotonic()
        for node in nodes:
            name = getattr(node, "__name__", str(node))

            # 时间预算：进入下一节点前检查，超限即中止（P2-3）
            if time.monotonic() - start > self.time_budget:
                raise PipelineTimeoutError(
                    f"管线时间预算 {self.time_budget:.1f}s 用尽（进入节点 {name} 前）"
                )

            # 条件短路：非 qa 意图跳过后续检索节点
            if pipeline.intent and pipeline.intent != "qa":
                logger.debug("Runner: 意图=%s, 跳过节点 %s", pipeline.intent, name)
                continue

            # 条件短路：缓存命中跳过检索节点
            if pipeline.from_cache and name in ("rewrite_query", "check_cache", "retrieve_chunks", "check_refuse"):
                logger.debug("Runner: 缓存命中, 跳过节点 %s", name)
                continue

            # 节点级重试
            for attempt in range(self.retry + 1):
                try:
                    pipeline = node(pipeline)
                    break  # 成功 → 跳出重试循环
                except Exception as e:
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
