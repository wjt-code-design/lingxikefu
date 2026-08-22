"""工单自动化测试：四种自动判定机制。

1. 客服首次发言 → open→processing
2. 用户满意反馈 → processing→resolved
3. 客服回复后超时 → processing→resolved
4. 用户长时间未响应 → open/processing→closed
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.feedback import Feedback
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User, UserRole
from app.services.ticket_automation import (
    auto_close_stale,
    auto_resolve_after_timeout,
    auto_resolve_on_positive_feedback,
    auto_start_processing,
)
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(scope="session", autouse=True)
def _patch_jsonb_for_sqlite():
    """SQLite 不支持 JSONB，替换为 JSON 类型 + 去掉 pg 专用默认值。"""
    from app.models.message import Message as _Msg

    for col in _Msg.__table__.columns:
        if col.name == "meta":
            col.type = JSON()
            col.server_default = None
            col.default = None
            col.nullable = True


# 调度器禁用由 tests/conftest.py 的 session 级 autouse fixture 统一负责（patch
# start/stop 为 no-op），此处不再重复定义。


API = "/api/v1"

USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
AGENT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client_and_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Session.__table__,
            Ticket.__table__,
            Message.__table__,
            Feedback.__table__,
        ],
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        d = Local()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_db] = _override
    init_db = Local()
    init_db.add(Session(id=SID, user_id=USER_ID))
    init_db.add_all([
        User(
            id=AGENT_ID, tenant_id="default",
            email="agent@test.com", password_hash="x",
            role=UserRole.agent, status="active",
        ),
        User(
            id=USER_ID, tenant_id="default",
            email="user@test.com", password_hash="x",
            role=UserRole.user, status="active",
        ),
    ])
    init_db.commit()
    init_db.close()
    test_db = Local()
    with TestClient(app) as c:
        try:
            yield c, test_db
        finally:
            test_db.close()
    app.dependency_overrides.clear()


@pytest.fixture
def client(client_and_db):
    c, _ = client_and_db
    return c


@pytest.fixture
def db(client_and_db):
    _, d = client_and_db
    return d


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT_ID), 'agent')}"}


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


# ===== Mechanism 1: 客服首次发言 → open→processing =====


def test_auto_start_processing_on_agent_message(client):
    """客服发消息后，该 session 的 open 工单自动流转为 processing。"""
    r = client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h())
    assert r.status_code == 201

    r2 = client.post(
        f"{API}/sessions/{SID}/messages",
        json={"content": "您好，请问有什么可以帮您？"},
        headers=_agent_h(),
    )
    assert r2.status_code == 201

    r3 = client.get(f"{API}/tickets", headers=_agent_h())
    t = r3.json()["items"][0]
    assert t["status"] == "processing"
    assert t["assignee_id"] == str(AGENT_ID)


def test_auto_start_processing_idempotent(db):
    """已经是 processing 的工单，再发消息不报错、不重复流转。"""
    t = Ticket(session_id=SID, status=TicketStatus.processing, tenant_id="default")
    db.add(t)
    db.commit()

    result = auto_start_processing(db, SID, AGENT_ID)
    assert result is None


def test_auto_start_processing_no_open_ticket(db):
    """session 下没有 open 工单时，返回 None，不报错。"""
    result = auto_start_processing(db, uuid.uuid4())
    assert result is None


# ===== Mechanism 2: 用户满意反馈 → processing→resolved =====


def test_auto_resolve_on_positive_feedback(client, db):
    """用户给 assistant 消息点 up 反馈后，processing 工单自动变 resolved。"""
    r = client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h())
    tid = r.json()["ticket_id"]
    client.patch(
        f"{API}/tickets/{tid}",
        json={"status": "processing", "version": 0},
        headers=_agent_h(),
    )

    msg = Message(
        tenant_id="default", session_id=SID, role=MessageRole.assistant,
        content="您好，这是为您查询到的信息。",
    )
    db.add(msg)
    db.commit()
    mid = msg.id

    r2 = client.post(
        f"{API}/messages/{mid}/feedback",
        json={"rating": "up", "comment": "解决得很清楚"},
        headers=_user_h(),
    )
    assert r2.status_code == 200

    r3 = client.get(f"{API}/tickets", headers=_agent_h())
    t = r3.json()["items"][0]
    assert t["status"] == "resolved"


def test_auto_resolve_only_on_up_feedback(client, db):
    """用户点 down 反馈不触发自动 resolved。"""
    t = Ticket(session_id=SID, status=TicketStatus.processing, tenant_id="default")
    db.add(t)
    db.commit()

    msg = Message(
        tenant_id="default", session_id=SID, role=MessageRole.assistant,
        content="回复内容",
    )
    db.add(msg)
    db.commit()
    mid = msg.id

    r = client.post(
        f"{API}/messages/{mid}/feedback",
        json={"rating": "down", "comment": "不满意"},
        headers=_user_h(),
    )
    assert r.status_code == 200

    db.refresh(t)
    assert t.status == TicketStatus.processing


def test_auto_resolve_idempotent(db):
    """已经是 resolved 的工单，再次反馈不报错。"""
    t = Ticket(session_id=SID, status=TicketStatus.resolved, tenant_id="default")
    db.add(t)
    db.commit()

    result = auto_resolve_on_positive_feedback(db, SID)
    assert result is None


# ===== Mechanism 3: 客服回复后超时 → processing→resolved =====


def test_auto_resolve_after_timeout(db):
    """客服回复超过 30 分钟无用户消息 → 自动 resolved。"""
    t = Ticket(session_id=SID, status=TicketStatus.processing, tenant_id="default")
    db.add(t)
    db.commit()
    tid = t.id

    old_msg = Message(
        tenant_id="default", session_id=SID, role=MessageRole.agent,
        content="客服回复，请您查看。",
    )
    old_msg.created_at = datetime.now(UTC) - timedelta(minutes=60)
    db.add(old_msg)
    db.commit()

    resolved = auto_resolve_after_timeout(db, timeout_minutes=30)
    assert len(resolved) >= 1
    assert resolved[0].status == TicketStatus.resolved
    assert resolved[0].id == tid


def test_auto_resolve_timeout_not_yet_due(db):
    """客服回复未超时 → 不触发自动 resolved。"""
    t = Ticket(session_id=SID, status=TicketStatus.processing, tenant_id="default")
    db.add(t)
    db.commit()

    recent_msg = Message(
        tenant_id="default", session_id=SID, role=MessageRole.agent,
        content="刚回复的消息。",
    )
    recent_msg.created_at = datetime.now(UTC) - timedelta(minutes=5)
    db.add(recent_msg)
    db.commit()

    resolved = auto_resolve_after_timeout(db, timeout_minutes=30)
    assert len(resolved) == 0


def test_auto_resolve_timeout_zero_disabled(db):
    """timeout=0 时关闭此功能：即使存在已超时（客服回复超 30 分钟无下文）的 processing
    工单也不流转。先造出"若开启则会流转"的数据，防止空库导致的假绿。"""
    t = Ticket(session_id=SID, status=TicketStatus.processing, tenant_id="default")
    db.add(t)
    old_msg = Message(
        tenant_id="default", session_id=SID, role=MessageRole.agent,
        content="客服回复，请您查看。",
    )
    old_msg.created_at = datetime.now(UTC) - timedelta(minutes=60)
    db.add(old_msg)
    db.commit()

    result = auto_resolve_after_timeout(db, timeout_minutes=0)
    assert result == []
    db.refresh(t)
    assert t.status == TicketStatus.processing


def test_auto_resolve_timeout_user_replied_after_agent(db):
    """agent 超时旧回复之后用户又发了消息 → 最后一条不是 agent，不自动 resolved。
    （守护候选查询的"之后无任何消息"语义：只看最后一条，不看是否有过 agent 回复。）"""
    t = Ticket(session_id=SID, status=TicketStatus.processing, tenant_id="default")
    db.add(t)
    old_agent = Message(
        tenant_id="default", session_id=SID, role=MessageRole.agent,
        content="客服回复，请您查看。",
    )
    old_agent.created_at = datetime.now(UTC) - timedelta(minutes=60)
    newer_user = Message(
        tenant_id="default", session_id=SID, role=MessageRole.user,
        content="我还有问题没解决",
    )
    newer_user.created_at = datetime.now(UTC) - timedelta(minutes=5)
    db.add_all([old_agent, newer_user])
    db.commit()

    resolved = auto_resolve_after_timeout(db, timeout_minutes=30)
    assert len(resolved) == 0
    db.refresh(t)
    assert t.status == TicketStatus.processing


def test_auto_resolve_timeout_multiple_agent_msgs_only_last_matters(db):
    """有多条 agent 消息时只看最后一条：最后一条未超时 → 不流转（旧的超时不算数）。"""
    t = Ticket(session_id=SID, status=TicketStatus.processing, tenant_id="default")
    db.add(t)
    m1 = Message(
        tenant_id="default", session_id=SID, role=MessageRole.agent,
        content="第一条（很久以前）",
    )
    m1.created_at = datetime.now(UTC) - timedelta(minutes=120)
    m2 = Message(
        tenant_id="default", session_id=SID, role=MessageRole.agent,
        content="第二条（刚回复）",
    )
    m2.created_at = datetime.now(UTC) - timedelta(minutes=5)
    db.add_all([m1, m2])
    db.commit()

    resolved = auto_resolve_after_timeout(db, timeout_minutes=30)
    assert len(resolved) == 0
    db.refresh(t)
    assert t.status == TicketStatus.processing


# ===== Mechanism 4: 用户长时间未响应 → open/processing→closed =====


def test_auto_close_stale_open_ticket(db):
    """open 工单超过 7 天未更新 → 自动 closed。"""
    t = Ticket(session_id=SID, status=TicketStatus.open, tenant_id="default")
    t.updated_at = datetime.now(UTC) - timedelta(days=10)
    db.add(t)
    db.commit()
    tid = t.id

    closed = auto_close_stale(db, idle_days=7)
    assert len(closed) >= 1
    assert closed[0].status == TicketStatus.closed
    assert closed[0].id == tid


def test_auto_close_stale_processing_ticket(db):
    """processing 工单超过 7 天未更新 → 自动 closed。"""
    t = Ticket(session_id=SID, status=TicketStatus.processing, tenant_id="default")
    t.updated_at = datetime.now(UTC) - timedelta(days=10)
    db.add(t)
    db.commit()
    tid = t.id

    closed = auto_close_stale(db, idle_days=7)
    assert any(c.id == tid for c in closed)


def test_auto_close_stale_recent_not_closed(db):
    """更新时间在阈值内的工单 → 不关闭。"""
    t = Ticket(session_id=SID, status=TicketStatus.open, tenant_id="default")
    t.updated_at = datetime.now(UTC) - timedelta(days=1)
    db.add(t)
    db.commit()

    closed = auto_close_stale(db, idle_days=7)
    assert len(closed) == 0


def test_auto_close_idle_days_zero_disabled(db):
    """idle_days=0 时关闭此功能：即使存在超期 10 天的 open 工单也不关闭（防假绿）。"""
    t = Ticket(session_id=SID, status=TicketStatus.open, tenant_id="default")
    t.updated_at = datetime.now(UTC) - timedelta(days=10)
    db.add(t)
    db.commit()

    result = auto_close_stale(db, idle_days=0)
    assert result == []
    db.refresh(t)
    assert t.status == TicketStatus.open


def test_auto_close_skips_resolved_closed(db):
    """resolved/closed 状态的工单不被 auto close。"""
    t_resolved = Ticket(
        session_id=SID, status=TicketStatus.resolved, tenant_id="default",
    )
    t_resolved.updated_at = datetime.now(UTC) - timedelta(days=30)
    t_closed = Ticket(
        session_id=SID, status=TicketStatus.closed, tenant_id="default",
    )
    t_closed.updated_at = datetime.now(UTC) - timedelta(days=30)
    db.add_all([t_resolved, t_closed])
    db.commit()

    closed = auto_close_stale(db, idle_days=7)
    assert len(closed) == 0
