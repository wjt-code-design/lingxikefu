"""T4 删除端点测试：会话删除（禁含未关闭工单）+ KB 删除。"""
from __future__ import annotations

import uuid

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Document, KnowledgeBase
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
ADMIN = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")
KBID = uuid.UUID("33333333-3333-3333-3333-333333333333")


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Session.__table__, Ticket.__table__, KnowledgeBase.__table__, Document.__table__],
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(User(id=UID, email="u@b.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(Session(id=SID, user_id=UID, tenant_id="default"))
        db.add(KnowledgeBase(id=KBID, name="kb", tenant_id="default"))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _h(uid, role):
    return {"Authorization": f"Bearer {create_access_token(str(uid), role)}"}


def test_delete_session_without_ticket(client):
    """无工单会话可删。"""
    r = client.delete(f"{API}/sessions/{SID}", headers=_h(UID, "user"))
    assert r.status_code == 200 and r.json()["ok"] is True


def test_delete_session_with_active_ticket_forbidden(client):
    """含未关闭工单的会话禁删（409）。"""
    agent_h = _h(uuid.uuid4(), "agent")
    # 建单（open）→ 再删会话 → 409
    r = client.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=agent_h)
    assert r.status_code == 201
    r2 = client.delete(f"{API}/sessions/{SID}", headers=_h(UID, "user"))
    assert r2.status_code == 409


def test_delete_kb(client, monkeypatch):
    """KB 删除（admin）；非 admin 403。"""
    monkeypatch.setattr("app.api.knowledge.vector_service.delete_by_doc_id", lambda doc_id: None)
    r = client.delete(f"{API}/knowledge-bases/{KBID}", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200 and r.json()["ok"] is True
    r2 = client.delete(f"{API}/knowledge-bases/{KBID}", headers=_h(UID, "user"))
    assert r2.status_code == 403
