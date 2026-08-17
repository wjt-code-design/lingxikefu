"""Customers API 测试（T6）：用户画像聚合（会话数/活跃度/未处理工单）+ 权限。"""
from __future__ import annotations

import uuid

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
AGENT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, Session.__table__, Ticket.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(User(id=UID, email="customer@b.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(Session(id=SID, user_id=UID, tenant_id="default"))
        db.add(Ticket(id=uuid.uuid4(), session_id=SID, tenant_id="default", status=TicketStatus.open))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT), 'agent')}"}


def test_customers_aggregation(client):
    """画像聚合：1 会话 + 1 未处理工单 + 最近活跃时间。"""
    r = client.get(f"{API}/customers", headers=_agent_h())
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    c = data["items"][0]
    assert c["account"] == "customer@b.com"
    assert c["session_count"] == 1
    assert c["open_tickets"] == 1
    assert c["last_active"]  # 会话更新时间


def test_customers_requires_agent(client):
    """普通用户无权访问画像。"""
    uh = {"Authorization": f"Bearer {create_access_token(str(UID), 'user')}"}
    assert client.get(f"{API}/customers", headers=uh).status_code == 403
