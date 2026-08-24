"""会话详情 conv_state 透出测试（批次B）：agent 可见 / user 视角不返回结构化状态。"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.message import Message, MessageSource
from app.models.session import Session
from app.models.user import User, UserRole
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
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine, tables=[Session.__table__, Message.__table__, User.__table__, MessageSource.__table__]
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
        db.add(User(id=AGENT_ID, role=UserRole.agent, email="agent@test.local", password_hash="x", status="active"))
        db.add(
            Session(
                id=SID,
                user_id=USER_ID,
                conv_state={"stage": "info_collecting", "topic": "退款", "slots": {}, "clarify_count": 0},
            )
        )
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT_ID), 'agent')}"}


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def test_detail_returns_conv_state_for_agent(client):
    """agent 视角：conv_state 结构化透出（客服观察用）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_agent_h())
    assert r.status_code == 200
    cs = r.json()["conv_state"]
    assert cs["topic"] == "退款"
    assert cs["stage"] == "info_collecting"


def test_detail_conv_state_none_for_old_session(client):
    """旧会话（conv_state=None）：字段返回 None，不报错。"""
    with TestClient(app) as c:
        # 新建无状态会话
        r = c.post(
            f"{API}/sessions",
            headers=_user_h(),
            json={"title": "t"},
        )
        new_sid = r.json()["session_id"]
        r2 = c.get(f"{API}/sessions/{new_sid}", headers=_agent_h())
        assert r2.status_code == 200
        assert r2.json()["conv_state"] is None
