"""工单领域服务（对抗审查 2026-08-27 下沉）：
    ensure_active_ticket 从 api 层迁入，供 api/tickets 与 TicketAgent 共用，
    消除 services→api 的反向依赖（旧版 TicketAgent 靠延迟导入规避循环）。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.session import Session as SessionModel
from app.models.ticket import Ticket, TicketStatus
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)


def _summary_text(summary: dict[str, Any] | str | None) -> str | None:
    """摘要归一化：dict（build_handoff_summary 产物）→ JSON 文本（ensure_ascii=False，
    DBA/坐席直读中文可读）；str 原样透传；None → NULL。"""
    if summary is None:
        return None
    if isinstance(summary, str):
        return summary
    return json.dumps(summary, ensure_ascii=False)


def ensure_active_ticket(
    db: OrmSession,
    session_id: uuid.UUID,
    message_id: uuid.UUID | None = None,
    source: str = "ai",
    notify: bool = True,
    summary: dict[str, Any] | str | None = None,
) -> Ticket | None:
    """AI 建单 helper（chat.py handoff 时调用）：幂等 + fail-open。

    同 session 已有 open/processing 工单 → 返回既有（不重复建，summary 亦不覆盖——
    首建摘要为准）；任何异常 → 回滚并返回 None（不阻断 SSE 流，chat 层降级为原话术）。
    notify=False：手动转人工时由 escalate_ticket 单独发 ticket.transfer，避免重复 ticket.created。
    summary：移交摘要（架构一期 4），TicketAgent 路径传 build_handoff_summary 产物
    持久化进 tickets.summary；manual 路径不传（保持 NULL）。
    """
    try:
        active = db.scalar(
            select(Ticket).where(
                Ticket.session_id == session_id,
                Ticket.status.in_([TicketStatus.open, TicketStatus.processing]),
            )
        )
        if active:
            return active
        t = Ticket(
            tenant_id=settings.TENANT_DEFAULT,
            session_id=session_id,
            message_id=message_id,
            source=source,
            summary=_summary_text(summary),
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        # 通知中心：AI handoff 建单成功 → 推给 agent（fail-open，不阻断问答流）
        if notify:
            create_notification(
                db,
                recipient_role="agent",
                event_type="ticket.created",
                title="新工单待处理",
                content=f"AI 转人工已建工单 {t.id}",
                resource_type="ticket",
                resource_id=str(t.id),
            )
        return t
    except Exception:  # noqa: BLE001 - fail-open：建单失败不影响问答流
        db.rollback()
        logger.exception("AI 建单失败（fail-open 降级）")
        return None


def draft_ticket_suggestion(
    ticket_id: str | uuid.UUID,
    question: str,
    trace_id: str = "",
    *,
    session_factory: Any = None,
) -> None:
    """低风险 handoff 建单后的 AI 预起草 worker（架构二期 1，fire-and-forget）。

    在独立线程/独立 DB 会话执行（请求级会话随响应关闭，禁止跨线程复用）：
    读工单 → 已有草稿跳过（首草为准，对齐 ensure_active_ticket 的 summary 幂等语义）
    → 复用坐席辅助核心（agent_assist.draft_reply：KB 定位 + top3 检索 + assist prompt
    + LLM 25s 非流式）→ 写 ``draft_suggestion`` + ``draft_kind="ai"``，坐席打开工单即见。

    fail-open：任何异常只记日志、草稿留空（NULL），绝不影响已完成的建单与问答流
    （本函数由 TicketAgent 经线程池调度时建单早已返回）。question 截 500 字与
    suggest 端点 SuggestReq 上限对齐。

    session_factory：DB 会话工厂（默认 SessionLocal）；测试注入 SQLite 工厂。
    """
    # 延迟导入：agent_assist 牵引 retrieval/vector/llm 链，避免加重本模块导入图
    import asyncio

    from app.services import agent_assist, conversation_state

    factory = session_factory or SessionLocal
    try:
        tid = uuid.UUID(str(ticket_id))
        with factory() as db:
            t = db.get(Ticket, tid)
            if t is None or not (question or "").strip():
                return
            if (t.draft_suggestion or "").strip():
                return  # 首草为准：不覆盖（可能已被坐席编辑，幂等复访也不重打 LLM）
            hint = None
            sess = db.get(SessionModel, t.session_id)
            if sess is not None:
                hint = conversation_state.to_prompt_hint(sess.conv_state)
            # SQLite（测试）下 Uuid 列可能读回 str，统一转 uuid 再入参（同 kb_lookup 惯例）
            sid = t.session_id if isinstance(t.session_id, uuid.UUID) else uuid.UUID(str(t.session_id))
            draft = asyncio.run(
                agent_assist.draft_reply(db, sid, question[:500], state_hint=hint)
            )
            if not draft.text:
                return  # fail-open：LLM 空/无知识库 → 草稿留空，不影响建单
            t.draft_suggestion = draft.text
            t.draft_kind = "ai"
            db.commit()
            logger.info(
                "ticket AI 预起草完成 ticket=%s trace_id=%s len=%d", tid, trace_id, len(draft.text)
            )
    except Exception:  # noqa: BLE001 - fail-open：预起草失败只留痕，不影响建单/问答
        logger.exception(
            "ticket AI 预起草失败（fail-open 草稿留空） ticket=%s trace_id=%s", ticket_id, trace_id
        )
