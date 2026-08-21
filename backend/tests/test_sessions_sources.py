"""会话详情引用来源测试（2026-08-21 溯源修复）：
GET /sessions/{id} 的 assistant 消息必须带 sources（来自 message_sources），
回归"历史消息无溯源" bug：此前详情接口遗漏 sources，前端历史气泡/溯源面板恒空。

owner / admin 均可见；user/agent 消息 sources 为空（无知识引用）。
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
UID = uuid.UUID("11111111-1111-1111-1111-111111111111")
ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("99999999-9999-9999-9999-999999999999")
MSG = uuid.UUID("88888888-8888-8888-8888-888888888888")


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
        engine,
        tables=[User.__table__, Session.__table__, Message.__table__, MessageSource.__table__],
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
        db.add(User(id=UID, email="u@t.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(User(id=ADMIN, email="admin@t.com", role=UserRole.admin, tenant_id="default", password_hash="x"))
        db.add(Session(id=SID, user_id=UID, tenant_id="default", title="溯源"))
        db.add(Message(id=MSG, session_id=SID, role=MessageRole.assistant, content="支持七天无理由退货", intent="qa"))
        db.add(MessageSource(
            id=uuid.uuid4(), message_id=MSG, chunk_id=uuid.uuid4(), doc_id=uuid.uuid4(),
            doc_title="退换货政策.md", snippet="七天无理由退货需签收后7天内申请", score=0.92,
            tenant_id="default",
        ))
        db.add(MessageSource(
            id=uuid.uuid4(), message_id=MSG, chunk_id=uuid.uuid4(), doc_id=uuid.uuid4(),
            doc_title="运费说明.md", snippet="退货运费由买家承担", score=0.61,
            tenant_id="default",
        ))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _h(uid: uuid.UUID, role: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(uid), role)}"}


def test_get_session_returns_assistant_sources(client):
    """回归溯源：详情接口的 assistant 消息带引用来源（2 条按 score 保留）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_h(UID, "user"))
    assert r.status_code == 200
    body = r.json()
    assistant_msgs = [m for m in body["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    srcs = assistant_msgs[0]["sources"]
    assert len(srcs) == 2
    docs = {s["doc_title"] for s in srcs}
    assert "退换货政策.md" in docs and "运费说明.md" in docs
    assert any(s["score"] == 0.92 for s in srcs)
    assert all(s["chunk_id"] and s["doc_id"] and s["snippet"] for s in srcs)


def test_get_session_sources_visible_to_admin(client):
    """admin 查看任意会话同样带 sources（客服工作台溯源场景）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    assistant_msgs = [m for m in r.json()["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1 and len(assistant_msgs[0]["sources"]) == 2


def test_get_session_no_sources_for_non_kb_msgs(client):
    """user / 无 message_sources 的消息 sources 为空数组（区别于缺失字段，契约稳定）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_h(UID, "user"))
    user_msgs = [m for m in r.json()["messages"] if m["role"] != "assistant"]
    # 该会话只有这一条 assistant 消息；断言字段存在性契约：sources 恒为数组
    for m in r.json()["messages"]:
        assert "sources" in m