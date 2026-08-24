"""Agent 抽象基类：统一 run(ctx) 契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.shared_context import SharedContext


class BaseAgent(ABC):
    """Agent 统一契约：读取 SharedContext，执行职责，结果写回 ctx 并返回。

    纪律（v1.1 方案书 §2）：
    - Agent 不直接互相调用，仅经 SharedContext 交换数据；
    - 降级必须写 ctx.degraded 留痕，禁止静默改路径。
    """

    name: str = "agent"

    @abstractmethod
    def run(self, ctx: SharedContext) -> SharedContext:
        """执行 Agent 逻辑（同步；阻塞调用由调用方 run_in_threadpool 搬出事件循环）。"""
        ...
