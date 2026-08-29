"""移交摘要持久化 + 工单流转时间戳（架构一期 4）。

- ensure_active_ticket(summary=...)：AI handoff 建单时把 build_handoff_summary 产物
  持久化进 tickets.summary（JSON 文本），坐席首屏直接看到主题/槽位/澄清状态，不再重问；
- 幂等：同 session 既有活跃工单直接复用，summary 不被覆盖（首建摘要为准）；
- 状态机 / PATCH 流转按目标状态补 processing_at / resolved_at。

手法沿用既有工单测试（test_ticket_agent / test_tickets）：SQLite in-memory +
Base.metadata.create_all 显式建表，不依赖真实 PG。
"""
from __future__ import annotations

import uuid

from app.models.base import Base
from app.models.notification import Notification
from app.models.ticket import Ticket, TicketStatus
from app.services.agents.ticket_agent import TicketAgent
from app.services.session_context import build_handoff_summary
from app.services.shared_context import SharedContext
from app.services.ticket_service import ensure_active_ticket
from app.services.ticket_state_machine import transition
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[Ticket.__table__, Notification.__table__])
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _summary() -> dict:
    """贴近真实移交现场：历史含诉求关键词，conv_state 含阶段/槽位/澄清计数。"""
    history = [{"role": "user", "content": "我要退款，到现在还没到账"}]
    conv_state = {
        "stage": "resolving",
        "topic": "退款",
        "slots": {"order_no": "SO123"},
        "clarify_count": 1,
    }
    return build_handoff_summary(history, conv_state)


# ── 移交摘要持久化 ────────────────────────────────────────────────────


def test_ticket_persists_handoff_summary():
    """建单传 summary（build_handoff_summary 产物）→ 落库为含主题/槽位的文本。"""
    db = _db()
    t = ensure_active_ticket(db, uuid.uuid4(), summary=_summary())
    assert t is not None
    assert t.summary
    assert "退款" in t.summary
    assert "SO123" in t.summary


def test_no_summary_keeps_null():
    """不传 summary（manual 路径/既有调用方）→ 保持 NULL，行为不变。"""
    db = _db()
    t = ensure_active_ticket(db, uuid.uuid4(), source="manual", notify=False)
    assert t is not None and t.summary is None


def test_idempotent_reuse_does_not_overwrite_summary():
    """幂等建单：同 session 既有活跃工单 → 复用既有，summary 不被新值覆盖。"""
    db = _db()
    sid = uuid.uuid4()
    first = ensure_active_ticket(db, sid, summary={"question": "第一手摘要"})
    again = ensure_active_ticket(db, sid, summary=_summary())
    assert again is not None and first is not None and again.id == first.id
    assert "第一手摘要" in again.summary
    assert "SO123" not in again.summary


async def test_ticket_agent_persists_ctx_handoff_summary():
    """TicketAgent 把 ctx.handoff_summary 带进建单（AI 路径注入点）。"""
    db = _db()
    ctx = SharedContext(
        query="投诉",
        intent="handoff",
        session_id=uuid.uuid4(),
        db=db,
        handoff_summary=_summary(),
    )
    ctx = await TicketAgent().run(ctx)
    assert ctx.ticket_id is not None
    t = db.get(Ticket, uuid.UUID(ctx.ticket_id))
    assert t is not None and "SO123" in (t.summary or "")


# ── 状态机流转时间戳 ──────────────────────────────────────────────────


def test_transition_to_processing_stamps_processing_at():
    db = _db()
    t = ensure_active_ticket(db, uuid.uuid4())
    assert t is not None and t.processing_at is None and t.resolved_at is None
    t2 = transition(db, t.id, "agent_first_reply")
    assert t2 is not None and t2.status == TicketStatus.processing
    assert t2.processing_at is not None
    assert t2.resolved_at is None


def test_transition_to_resolved_stamps_resolved_at():
    db = _db()
    t = ensure_active_ticket(db, uuid.uuid4())
    assert t is not None
    transition(db, t.id, "agent_first_reply")
    t2 = transition(db, t.id, "positive_feedback")
    assert t2 is not None and t2.status == TicketStatus.resolved
    assert t2.resolved_at is not None


def test_transition_to_closed_no_stamp():
    """closed 无独立列（updated_at 已覆盖）→ 两个时间戳都不动。"""
    db = _db()
    t = ensure_active_ticket(db, uuid.uuid4())
    assert t is not None
    t2 = transition(db, t.id, "idle_timeout")
    assert t2 is not None and t2.status == TicketStatus.closed
    assert t2.processing_at is None and t2.resolved_at is None
