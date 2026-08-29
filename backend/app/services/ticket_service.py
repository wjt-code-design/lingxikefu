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
