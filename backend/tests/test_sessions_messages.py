"""Sessions 人工客服消息端点测试（Branch 3）：POST /sessions/{id}/messages 权限 / 落库 / 透出。

覆盖：
- 无凭证 → 401（HTTPBearer）；
- 普通 user 代发 → 403（仅 admin/agent）；
- agent 代发 → 201，role=agent，agent_name 取操作人 email；
- 空白内容 → 422（strip 后为空）；
- 落库后顾客（会话 owner）GET 详情可见 role=agent + agent_name；
- 会话不存在 → 404。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.message import Message, MessageRole, MessageSource
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
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # messages.meta 是 JSONB + PG server_default（SQLite 无法编译）→ 建表前替换
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine,
        tables=[Session.__table__, Message.__table__, User.__table__, MessageSource.__table__],
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
        db.add(
            User(
                id=AGENT_ID,
                role=UserRole.agent,
                email="agent@test.local",
                password_hash="x",
                status="active",
            )
        )
        db.add(Session(id=SID, user_id=USER_ID))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT_ID), 'agent')}"}


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def test_agent_message_requires_auth(client):
    """Branch 3：无凭证 → 401。"""
    r = client.post(f"{API}/sessions/{SID}/messages", json={"content": "hi"})
    assert r.status_code == 401


def test_agent_message_forbidden_for_user(client):
    """Branch 3：普通 user 不能代发 agent 消息 → 403。"""
    r = client.post(f"{API}/sessions/{SID}/messages", json={"content": "hi"}, headers=_user_h())
    assert r.status_code == 403


def test_agent_message_created_by_staff(client):
    """Branch 3：agent 代发 → 201，role=agent，agent_name 取操作人 email。"""
    r = client.post(
        f"{API}/sessions/{SID}/messages",
        json={"content": "您好，我是人工客服"},
        headers=_agent_h(),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "agent"
    assert body["agent_name"] == "agent@test.local"
    assert body["content"] == "您好，我是人工客服"


def test_agent_message_blank_content_rejected(client):
    """Branch 3：空白内容 → 422（strip 后为空）。"""
    r = client.post(f"{API}/sessions/{SID}/messages", json={"content": "   "}, headers=_agent_h())
    assert r.status_code == 422


def test_agent_message_persisted_and_exposed_to_customer(client):
    """Branch 3：落库 + 顾客（会话 owner）GET 详情可见 role=agent + agent_name。"""
    r = client.post(
        f"{API}/sessions/{SID}/messages",
        json={"content": "我来帮您处理"},
        headers=_agent_h(),
    )
    assert r.status_code == 201
    mid = r.json()["id"]

    d = client.get(f"{API}/sessions/{SID}", headers=_user_h())
    assert d.status_code == 200
    hit = [m for m in d.json()["messages"] if m["role"] == "agent" and m["id"] == mid]
    assert hit and hit[0]["agent_name"] == "agent@test.local"


def test_agent_message_missing_session_404(client):
    """Branch 3：会话不存在 → 404。"""
    r = client.post(
        f"{API}/sessions/{uuid.uuid4()}/messages",
        json={"content": "hi"},
        headers=_agent_h(),
    )
    assert r.status_code == 404


def test_session_detail_exposes_tool_marker(client):
    """大扫查 F-major（2026-08-25）：历史加载透出 meta.tool——工具徽章读路径。

    写路径已落库（chat.py _persist_answer 写 meta["tool"]），但详情接口此前不透出 →
    刷新后气泡徽章消失、客服 observe 永不显示（数据在、读路断）。
    断言：assistant 带 tool 的消息透出 tool；无 tool 消息该字段为 None。"""
    from app.core.database import get_db

    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        db.add_all([
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="订单已发货",
                    meta={"tool": "order_query"}),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="普通回答", meta={}),
        ])
        db.commit()
    finally:
        gen.close()

    d = client.get(f"{API}/sessions/{SID}", headers=_user_h())
    assert d.status_code == 200
    msgs = d.json()["messages"]
    with_tool = [m for m in msgs if m["content"] == "订单已发货"]
    without_tool = [m for m in msgs if m["content"] == "普通回答"]
    assert with_tool and with_tool[0]["tool"] == "order_query"
    assert without_tool and without_tool[0].get("tool") is None
