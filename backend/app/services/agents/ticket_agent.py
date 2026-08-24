"""Ticket Agent（v1.1 方案书 §2.3）：薄包装 ensure_active_ticket。

定位澄清（评审修订）：
- 现有 ``ticket_automation`` 是生命周期/定时自动化（客服首发言、满意反馈、超时），
  不在问答请求路径上，**不是**本 Agent 的封装对象；
- 请求路径上的建单能力已存在于 ``tickets.ensure_active_ticket``（幂等 + fail-open），
  本 Agent 仅做薄包装，不重复实现状态流转；
- 情绪分级由 Router 前置的 classify_intent 完成（单一真源），本 Agent 不再做情绪分类。
"""
from __future__ import annotations

import logging

from app.services.agents.base import BaseAgent
from app.services.shared_context import SharedContext

logger = logging.getLogger(__name__)


class TicketAgent(BaseAgent):
    """工单 Agent：handoff 场景幂等建单（fail-open，建单失败不阻断问答流）。"""

    name = "ticket_agent"

    def run(self, ctx: SharedContext) -> SharedContext:
        if ctx.intent != "handoff":
            return ctx  # 非 handoff 不建单
        if ctx.session_id is None:
            ctx.degraded.append("ticket:no_session")
            return ctx
        if ctx.db is None:
            ctx.degraded.append("ticket:no_db")
            return ctx
        # 延迟导入：避免 services 层对 api 层的顶层依赖（tickets 属于 api 层）
        from app.api.tickets import ensure_active_ticket

        try:
            # 复用请求级会话（chat 层注入）：同事务语义 + 测试可用依赖覆盖的会话
            t = ensure_active_ticket(ctx.db, ctx.session_id, ctx.message_id)
            if t is not None:
                ctx.ticket_id = str(t.id)
            else:
                ctx.degraded.append("ticket:create_failed")  # fail-open 留痕
        except Exception:  # noqa: BLE001 - 与 ensure_active_ticket 同口径：建单不阻断问答
            logger.exception("TicketAgent 建单异常（降级留痕）")
            ctx.degraded.append("ticket:exception")
        return ctx
