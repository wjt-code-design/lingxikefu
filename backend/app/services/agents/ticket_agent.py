"""Ticket Agent（v1.1 方案书 §2.3）：薄包装 ticket_service.ensure_active_ticket。

定位澄清（评审修订 + 对抗审查 2026-08-27）：
- 现有 ``ticket_automation`` 是生命周期/定时自动化（客服首发言、满意反馈、超时），
  不在问答请求路径上，**不是**本 Agent 的封装对象；
- 请求路径上的建单能力在 ``services.ticket_service.ensure_active_ticket``
  （幂等 + fail-open），本 Agent 仅做薄包装，不重复实现状态流转；
- 情绪分级由 Router 前置的 classify_intent 完成（单一真源），本 Agent 不再做情绪分类；
- **统一 async 契约**：run 为 coroutine，内部同步建单逻辑经 run_in_threadpool
  执行（不阻塞事件循环）；编排方一律 ``await agent.run(ctx)``。

架构二期 1（L2 预起草）：low risk handoff 建单后 fire-and-forget 调度 AI 预起草
（ticket_service.draft_ticket_suggestion 写 tickets.draft_suggestion）——起草含检索
+LLM（≤25s），建单已在 SSE 响应流内完成，绝不能同步等待；失败仅降级留痕。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi.concurrency import run_in_threadpool

from app.services.agents.base import BaseAgent
from app.services.shared_context import SharedContext
from app.services.ticket_service import draft_ticket_suggestion, ensure_active_ticket

logger = logging.getLogger(__name__)

#: 预起草 fire-and-forget 线程池（M4 教训的落法：**池而非裸 Thread**——
#: 池对象模块级持有线程引用，无「未保存引用被 GC」风险；max_workers 有界，
#: handoff 突发时排队而非线程风暴；thread_name_prefix 便于日志/转储定位）。
#: 线程非 daemon：解释器退出时 atexit join，单任务上限 = LLM 25s + 一次 DB 写，
#: 不会拖垮关停。max_workers=2：起草是尽力而为的后台任务，低频事件，2 并发足够
#: 且不放大 DB/LLM 压力。
_draft_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ticket-draft")


def _schedule_draft(ticket_id: str, question: str, trace_id: str = "") -> None:
    """低风险 handoff 建单后的后台预起草调度（fire-and-forget，不阻塞 SSE）。"""
    _draft_pool.submit(draft_ticket_suggestion, ticket_id, question, trace_id)


class TicketAgent(BaseAgent):
    """工单 Agent：handoff 场景幂等建单（fail-open，建单失败不阻断问答流）。"""

    name = "ticket_agent"

    async def run(self, ctx: SharedContext) -> SharedContext:
        if ctx.intent != "handoff":
            return ctx  # 非 handoff 不建单
        if ctx.session_id is None:
            ctx.degraded.append("ticket:no_session")
            return ctx
        if ctx.db is None:
            ctx.degraded.append("ticket:no_db")
            return ctx
        # 同步建单（DB 读写）经线程池执行，不阻塞事件循环
        try:
            ticket_id = await run_in_threadpool(self._ensure, ctx)
            if ticket_id is not None:
                ctx.ticket_id = ticket_id
                # 架构二期 1：low risk（知识型，非显式转人工/投诉/情绪）→ 后台 AI 预起草。
                # handoff_risk 未判别（""）时保守不预起草——只认显式 low。
                if ctx.handoff_risk == "low":
                    try:
                        _schedule_draft(ticket_id, ctx.query, ctx.trace_id)
                    except Exception:  # noqa: BLE001 - 调度失败不影响建单结果
                        logger.exception("预起草调度失败（fail-open）")
                        ctx.degraded.append("ticket:draft_schedule_failed")
            else:
                ctx.degraded.append("ticket:create_failed")  # fail-open 留痕
        except Exception:  # noqa: BLE001 - 与 ensure_active_ticket 同口径：建单不阻断问答
            logger.exception("TicketAgent 建单异常（降级留痕）")
            ctx.degraded.append("ticket:exception")
        return ctx

    @staticmethod
    def _ensure(ctx: SharedContext) -> str | None:
        """同步建单体：返回工单 id 或 None（复用请求级会话，同事务语义）。"""
        # run() 已做 no_session 守卫；此处为静态类型收窄 + 防御性兜底
        if ctx.session_id is None:
            return None
        t = ensure_active_ticket(
            ctx.db, ctx.session_id, ctx.message_id, summary=ctx.handoff_summary
        )
        return str(t.id) if t is not None else None
