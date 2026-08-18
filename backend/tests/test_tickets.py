"""Tickets API 测试（T1 工单闭环）：建单幂等 / 列表过滤 / 状态流转 / 权限 / S2 乐观锁。"""
from __future__ import annotations

import uuid

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
AGENT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[Session.__table__, Ticket.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(Session(id=SID, user_id=USER_ID))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT_ID), 'agent')}"}


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def test_create_ticket_and_idempotent(client):
    """建单 + 幂等：同 session 二次建单返回同一工单。"""
    r = client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h())
    assert r.status_code == 201
    t1 = r.json()
    assert t1["status"] == "open" and t1["session_id"] == str(SID)

    r2 = client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h())
    assert r2.status_code == 201
    assert r2.json()["ticket_id"] == t1["ticket_id"]  # 幂等：不重复建


def test_list_tickets_status_filter(client):
    """列表 + status 过滤。"""
    client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h())
    r = client.get(f"{API}/tickets", headers=_agent_h())
    assert r.status_code == 200 and r.json()["total"] == 1
    r2 = client.get(f"{API}/tickets?status=closed", headers=_agent_h())
    assert r2.json()["total"] == 0


def test_status_transition_valid_and_invalid(client):
    """合法迁移 open→processing→resolved→closed；非法迁移（open→resolved 直接跳）拒绝。"""
    t0 = client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h()).json()
    tid = t0["ticket_id"]
    assert t0["version"] == 0  # S2：新建工单版本从 0 起
    # open → processing（version 0 → 1）
    r = client.patch(
        f"{API}/tickets/{tid}",
        json={"status": "processing", "assignee_id": str(AGENT_ID), "version": 0},
        headers=_agent_h(),
    )
    assert r.status_code == 200 and r.json()["status"] == "processing"
    assert r.json()["assignee_id"] == str(AGENT_ID) and r.json()["version"] == 1
    # processing → resolved → closed（version 1 → 2 → 3）
    assert (
        client.patch(f"{API}/tickets/{tid}", json={"status": "resolved", "version": 1}, headers=_agent_h()).json()["version"]
        == 2
    )
    assert (
        client.patch(f"{API}/tickets/{tid}", json={"status": "closed", "version": 2}, headers=_agent_h()).json()["version"]
        == 3
    )
    # closed 终态：不可再流转
    r = client.patch(f"{API}/tickets/{tid}", json={"status": "open", "version": 3}, headers=_agent_h())
    assert r.status_code == 400


def test_update_ticket_optimistic_lock_conflict(client):
    """S2：并发更新 version 不匹配 → 409（防后者静默覆盖，审计与实况一致）。"""
    t0 = client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h()).json()
    tid = t0["ticket_id"]
    # 客服 A 用 version 0 更新成功 → version 变 1
    r1 = client.patch(f"{API}/tickets/{tid}", json={"status": "processing", "version": 0}, headers=_agent_h())
    assert r1.status_code == 200 and r1.json()["version"] == 1
    # 客服 B 仍持旧 version 0 提交 → 409
    r2 = client.patch(f"{API}/tickets/{tid}", json={"status": "resolved", "version": 0}, headers=_agent_h())
    assert r2.status_code == 409
    # 刷新拿到最新 version 1 再更新 → 成功
    r3 = client.patch(f"{API}/tickets/{tid}", json={"status": "resolved", "version": 1}, headers=_agent_h())
    assert r3.status_code == 200 and r3.json()["version"] == 2


def test_user_cannot_access_tickets(client):
    """普通用户无权建单/列表/流转。"""
    assert client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_user_h()).status_code == 403
    assert client.get(f"{API}/tickets", headers=_user_h()).status_code == 403


def test_escalate_creates_manual_ticket(client):
    """P0-4：用户主动转人工 → escalate 建单（source=manual）+ 幂等。"""
    r = client.post(f"{API}/tickets/escalate/{SID}", headers=_user_h())
    assert r.status_code == 201
    t = r.json()
    assert t["status"] == "open" and t["source"] == "manual"
    # 幂等：二次 escalate 返回同一工单
    r2 = client.post(f"{API}/tickets/escalate/{SID}", headers=_user_h())
    assert r2.status_code == 201 and r2.json()["ticket_id"] == t["ticket_id"]


def test_escalate_other_user_session_forbidden(client):
    """P0-4：不能升级别人的会话（越权 403）。"""
    OTHER = uuid.UUID("33333333-3333-3333-3333-333333333333")
    r = client.post(f"{API}/tickets/escalate/{SID}", headers={
        "Authorization": f"Bearer {create_access_token(str(OTHER), 'user')}"
    })
    assert r.status_code == 403


def test_escalate_missing_session_404(client):
    """P0-4：不存在的会话 → 404。"""
    r = client.post(f"{API}/tickets/escalate/{uuid.uuid4()}", headers=_user_h())
    assert r.status_code == 404


def test_my_tickets_lists_own_only(client):
    """P2-1：GET /tickets/my 只返回当前用户会话的工单（user 可调，跨用户隔离）。"""
    OTHER = uuid.UUID("44444444-4444-4444-4444-444444444444")
    # 通过 agent 建当前用户会话的工单
    r = client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h())
    assert r.status_code == 201
    # user 查自己的工单 → 应有 1 条（SID 属于 USER_ID）
    r2 = client.get(f"{API}/tickets/my", headers=_user_h())
    assert r2.status_code == 200
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["session_id"] == str(SID)
    # 另一个用户查 → 0 条（隔离）
    r3 = client.get(f"{API}/tickets/my", headers={
        "Authorization": f"Bearer {create_access_token(str(OTHER), 'user')}"
    })
    assert r3.status_code == 200 and r3.json()["total"] == 0
