"""QA Agent（v1.1 方案书 §2.3）：封装现有 RAG 管线，不新增检索/生成逻辑。

两个入口：
- ``run(ctx)``：非流式（run_pipeline），结果写 ``ctx.rag_result``——供评测 /
  批量 / 未来非流式端点使用；
- 流式输出保留在 chat 层（``stream_answer``）：SSE token 流与断连回滚、
  首字时延埋点、落库、缓存回填全部耦合在 chat 层（方案书 §2.2 硬约束），
  Agent 不接管流式职责。

intent 对齐说明：Router 已用同一真源函数（classify_intent）前置分类；
run_pipeline 内部首节点对同一原文重算，结果必然一致——此处不再做第二份判定。
"""
from __future__ import annotations

from app.services.agents.base import BaseAgent
from app.services.rag_service import run_pipeline
from app.services.shared_context import SharedContext


class QAAgent(BaseAgent):
    """问答 Agent：非流式管线入口（流式在 chat 层）。"""

    name = "qa_agent"

    def run(self, ctx: SharedContext) -> SharedContext:
        if ctx.kb_id is None:
            ctx.degraded.append("qa:no_kb")
            return ctx
        # 图片通道接入后，fused_query（图+文融合）优先于原文进入检索/缓存键
        query = ctx.fused_query or ctx.query
        ctx.rag_result = run_pipeline(
            query,
            ctx.kb_id,
            history=ctx.history,
            kb_version=ctx.kb_version,
        )
        return ctx
