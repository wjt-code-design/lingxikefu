"""Router（v1.1 方案书 §2.1）：前置意图分类（单一真源）+ 规则分发 + 执行计划。

纪律（教训库对照 + 对抗审查 2026-08-27）：
- 分类逻辑只调用 ``rag_service.classify_intent``，禁止复制关键词表（单一真源）；
- Router 不直接调用 Agent，只输出执行计划（agents_invoked），由 chat 层按计划执行；
- **执行计划只说真话**：agents_invoked 只列出 chat 层真实编排执行的 Agent
  （image / ticket）。QA 路径由 chat 层直接调 ``stream_answer``（非 Agent 成员，
  设计文档的 qa_agent 包装类无生产调用方，已随审查删除）——计划里不再出现
  一个永不执行的 "qa_agent" 假成员。
- Router 不产出/攒 SSE 事件：流式永远由 chat 层逐事件发出（攒列表会击穿
  断连回滚 / 首字埋点等既有行为）。

分发规则（真实执行）：
1. 有图片引用（image_paths/image_refs）→ Image Agent 先行（fused_query 喂给
   stream_answer 的检索/缓存键）
2. intent=handoff（人工/情绪词）→ Ticket Agent 建单（幂等 + fail-open）
3. 其余（qa / chitchat）→ 无 Agent 编排，chat 层直走 stream_answer
"""
from __future__ import annotations

import logging

from app.services.rag_service import classify_intent
from app.services.shared_context import SharedContext

logger = logging.getLogger(__name__)

TICKET_AGENT = "ticket_agent"
IMAGE_AGENT = "image_agent"


class Router:
    """请求级分发：前置分类 → 决定 agents_invoked（执行计划，只含真实编排成员）。"""

    def route(self, ctx: SharedContext) -> SharedContext:
        # 前置分类：单一真源（规则式，零 LLM，与管线内 intent 节点同源必然一致）
        ctx.intent = classify_intent(ctx.query)

        agents: list[str] = []
        # v1.3：图片通道实际数据源是 image_paths（chat 层注入文件路径，Image Agent 消费）；
        # image_refs 为预留引用字段（当前恒空）——任一有值即排 Image Agent 先行
        if ctx.image_paths or ctx.image_refs:
            agents.append(IMAGE_AGENT)  # Image Agent 必走且先行
        if ctx.intent == "handoff":
            agents.append(TICKET_AGENT)  # 投诉/情绪词命中 → 并行建单

        ctx.agents_invoked = agents
        return ctx


#: 模块级单例（无状态，可安全共享）
router = Router()
