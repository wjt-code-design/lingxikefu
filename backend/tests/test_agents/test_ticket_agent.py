"""Ticket Agent 测试：幂等建单 + fail-open 降级留痕 + 边界守卫。"""
from __future__ import annotations

import uuid

import pytest
from app.models.base import Base
from app.models.notification import Notification
from app.models.ticket import Ticket, TicketStatus
from app.services.agents.ticket_agent import TicketAgent
from app.services.shared_context import SharedContext
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[Ticket.__table__, Notification.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    with Local() as s:
        yield s


def _ctx(db, intent="handoff") -> SharedContext:
    return SharedContext(
        query="我要投诉",
        intent=intent,
        session_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        db=db,
    )


def test_handoff_creates_ticket(db_session):
    ctx = TicketAgent().run(_ctx(db_session))
    assert ctx.ticket_id is not None
    rows = db_session.scalars(select(Ticket)).all()
    assert len(rows) == 1
    assert str(rows[0].id) == ctx.ticket_id
    assert rows[0].status == TicketStatus.open


def test_non_handoff_no_ticket(db_session):
    ctx = TicketAgent().run(_ctx(db_session, intent="qa"))
    assert ctx.ticket_id is None
    assert ctx.degraded == []
    assert db_session.scalars(select(Ticket)).all() == []


def test_idempotent_reuses_active_ticket(db_session):
    sid = uuid.uuid4()
    existing = Ticket(tenant_id="default", session_id=sid, status=TicketStatus.open)
    db_session.add(existing)
    db_session.commit()

    ctx = SharedContext(query="投诉", intent="handoff", session_id=sid, db=db_session)
    ctx = TicketAgent().run(ctx)
    assert ctx.ticket_id == str(existing.id)  # 复用既有，不重复建
    assert len(db_session.scalars(select(Ticket)).all()) == 1


def test_missing_session_degraded(db_session):
    ctx = SharedContext(query="投诉", intent="handoff", session_id=None, db=db_session)
    ctx = TicketAgent().run(ctx)
    assert ctx.ticket_id is None
    assert "ticket:no_session" in ctx.degraded


def test_missing_db_degraded():
    ctx = SharedContext(query="投诉", intent="handoff", session_id=uuid.uuid4(), db=None)
    ctx = TicketAgent().run(ctx)
    assert ctx.ticket_id is None
    assert "ticket:no_db" in ctx.degraded


def test_db_failure_fail_open_with_trace(db_session):
    """DB 异常 → fail-open：不抛出、降级留痕（建单失败不阻断问答流）。"""

    class Boom:
        def scalar(self, *_a, **_k):
            raise RuntimeError("db down")

        def rollback(self):
            pass

    ctx = SharedContext(query="投诉", intent="handoff", session_id=uuid.uuid4(), db=Boom())
    ctx = TicketAgent().run(ctx)  # 不得抛出
    assert ctx.ticket_id is None
    assert any(d.startswith("ticket:") for d in ctx.degraded)
