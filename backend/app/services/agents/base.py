"""Agent 抽象基类：统一 async run(ctx) 契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.shared_context import SharedContext


class BaseAgent(ABC):
    """Agent 统一契约：读取 SharedContext，执行职责，结果写回 ctx 并返回。

    纪律（v1.1 方案书 §2 + 对抗审查 2026-08-27）：
    - Agent 不直接互相调用，仅经 SharedContext 交换数据；
    - 降级必须写 ctx.degraded 留痕，禁止静默改路径；
    - **统一 async 契约**：run 恒为 coroutine，编排方一律 ``await agent.run(ctx)``。
      阻塞式同步实现（DB/IO）在 Agent 内部经 run_in_threadpool 搬出事件循环，
      对外不暴露"哪个 Agent 要 await、哪个要 run_in_threadpool"的差异。
    """

    name: str = "agent"

    @abstractmethod
    async def run(self, ctx: SharedContext) -> SharedContext:
        """执行 Agent 逻辑（async；内部阻塞操作自行 run_in_threadpool）。

        旧版契约的问题：base 声明同步 run、ImageAgent 却是 async（mypy
        [override] 实锤），编排方被迫按 Agent 区分 await/线程池两种调用形态。
        """
        ...
