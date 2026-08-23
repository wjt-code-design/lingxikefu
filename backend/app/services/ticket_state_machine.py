"""工单状态机：显式状态流转表 + 统一 transition() 入口。

设计（vet-plan 裁定）：
- 单一真源：TRANSITIONS 表定义所有合法流转，新增状态/事件只改此表
- 幂等：重复流转返回 None（CAS 让位，不覆盖）
- 零依赖：纯 dict + 枚举，不引 transitions 库
- 现有 auto_* 函数保留作为调用方（已有完整测试），本模块做状态校验层

状态流转全貌：
    open ──agent_first_reply──► processing ──positive_feedback────► resolved
     │                              │
     └──idle_timeout───────────────┘
                                    └──agent_reply_timeout────► resolved
     └──idle_timeout──────────────► closed
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.tenant import get_current_tenant
from app.models.ticket import Ticket, TicketStatus

logger = logging.getLogger(__name__)


# ── 状态流转表（单一真源）──────────────────────────────────────────────
# 当前状态 → 事件 → 目标状态
TRANSITIONS: dict[TicketStatus, dict[str, TicketStatus]] = {
    TicketStatus.open: {
        "agent_first_reply": TicketStatus.processing,
        "idle_timeout": TicketStatus.closed,
    },
    TicketStatus.processing: {
        "positive_feedback": TicketStatus.resolved,
        "agent_reply_timeout": TicketStatus.resolved,
        "idle_timeout": TicketStatus.closed,
    },
    TicketStatus.resolved: {},  # 终态：无出边
    TicketStatus.closed: {},    # 终态：无出边
}


def can_transition(from_status: TicketStatus, event: str) -> bool:
    """校验流转是否合法。不在表中的流转拒绝（防御未来状态漂移）。"""
    return event in TRANSITIONS.get(from_status, {})


def transition(
    db: OrmSession,
    ticket_id: uuid.UUID,
    event: str,
    *,
    assignee_id: uuid.UUID | None = None,
) -> Ticket | None:
    """统一状态流转入口：校验 → CAS 写 → 返回更新后的工单。

    - 校验：当前状态 + 事件必须在 TRANSITIONS 表中
    - CAS：WHERE 含预期状态 + version，并发让位
    - 幂等：rowcount=0（已被并发流转）返回 None
    - 失败：非法流转返回 None（不抛，fail-open 让调用方决定）
    """
    # 取当前工单
    t = db.get(Ticket, ticket_id)
    if t is None:
        return None
    if t.tenant_id != get_current_tenant():
        return None

    from_status = t.status
    if not can_transition(from_status, event):
        logger.warning(
            "ticket_sm: 非法流转 %s + %s → 拒绝 (ticket=%s)",
            from_status.value,
            event,
            ticket_id,
        )
        return None

    to_status = TRANSITIONS[from_status][event]

    # CAS 写：预期当前状态 + version，并发让位
    result = db.execute(
        update(Ticket)
        .where(
            Ticket.id == ticket_id,
            Ticket.status == from_status,
            Ticket.version == t.version,
            Ticket.tenant_id == get_current_tenant(),
        )
        .values(
            status=to_status,
            version=Ticket.version + 1,
            updated_at=datetime.now(UTC),
            **({"assignee_id": assignee_id} if assignee_id else {}),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()

    if result.rowcount == 0:
        logger.info(
            "ticket_sm: CAS 让位 %s → %s (ticket=%s)",
            from_status.value,
            to_status.value,
            ticket_id,
        )
        return None

    db.refresh(t)
    logger.info(
        "ticket_sm: %s → %s (event=%s, ticket=%s)",
        from_status.value,
        to_status.value,
        event,
        ticket_id,
    )
    return t
