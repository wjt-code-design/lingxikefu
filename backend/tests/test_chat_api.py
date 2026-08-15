"""Chat API 测试（BU-06）：SSE 事件顺序 / 会话归属 / 配额 / 来源落库。

- SQLite StaticPool + get_db 覆盖；建 session/message/message_sources/kb 表；
- mock stream_answer（直接 yield 契约事件序列），不依赖真实 RAG/Qdrant/百炼；
- mock quota（避免 Redis 依赖）。
"""
from __future__ import annotations

import app.models.knowledge  # noqa: F401
import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Document, KnowledgeBase
from app.models.message import Message, MessageRole, MessageSource
from app.models.session import Session
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # messages.meta 是 JSONB + PG server_default（SQLite 无法编译）→ 建表前替换
    import sqlalchemy as sa

    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine,
        tables=[
            Session.__table__,
            Message.__table__,
            MessageSource.__table__,
            KnowledgeBase.__table__,
            Document.__table__,
        ],
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override

    # 初始数据：session + kb（sa.Uuid 列需传 uuid.UUID 对象，SQLite 不接受字符串）
    import uuid as _uuid

    with Local() as db:
        db.add(Session(id=_uuid.UUID("11111111-1111-1111-1111-111111111111"), user_id=_uuid.UUID("22222222-2222-2222-2222-222222222222")))
        db.add(KnowledgeBase(id=_uuid.UUID("33333333-3333-3333-3333-333333333333"), name="星河测试库"))
        db.commit()

    # mock quota：余额充足，记录 increment 调用
    calls = {"increments": 0}

    class FakeQuota:
        def left_today(self, _uid):
            return 10

        def increment(self, _uid, n=1):
            calls["increments"] += n

    monkeypatch.setattr("app.api.chat.get_quota_service", lambda: FakeQuota())
    monkeypatch.setattr(
        "app.api.chat._latest_kb_id",
        lambda db: "33333333-3333-3333-3333-333333333333",
    )

    with TestClient(app) as c:
        yield c, Local, calls
    app.dependency_overrides.clear()


def _headers():
    return {"Authorization": f"Bearer {create_access_token('22222222-2222-2222-2222-222222222222', 'user')}"}


class _FakeStream:
    """模拟 stream_answer：按契约事件序列 yield（token → sources → done）。"""

    @staticmethod
    async def __call__(query, kb_id, history=None, top_k=5):
        yield ("stage", {"stage": "retrieving"})
        yield ("stage", {"stage": "generating"})
        yield ("token", {"delta": "保修"})
        yield ("token", {"delta": "12个月"})
        yield (
            "sources",
            {
                "sources": [
                    {
                        "chunk_id": "44444444-4444-4444-4444-444444444444",
                        "doc_id": "55555555-5555-5555-5555-555555555555",
                        "score": 0.9,
                        "snippet": "保修期12个月",
                    }
                ]
            },
        )
        yield ("done", {"message_id": ""})


def test_chat_stream_events_and_persist(client, monkeypatch):
    tc, Local, calls = client
    monkeypatch.setattr("app.api.chat.stream_answer", _FakeStream())

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "保修多久", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    body = r.text
    # 事件顺序：stage retrieving → stage generating → token*2 → sources → done
    events = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    assert len(events) == 6
    assert '"retrieving"' in events[0] and '"generating"' in events[1]
    assert '"保修"' in events[2] and '"12个月"' in events[3]
    assert '"sources"' in events[4]
    assert '"done"' in events[5]

    # 落库：user + assistant 两条消息，1 条 source，配额已扣
    with Local() as db:
        msgs = db.scalars(select(Message)).all()
        roles = sorted(m.role.value for m in msgs)
        assert roles == ["assistant", "user"]
        assistant = next(m for m in msgs if m.role == MessageRole.assistant)
        assert "保修" in assistant.content and "12个月" in assistant.content
        srcs = db.scalars(select(MessageSource)).all()
        assert len(srcs) == 1
        assert srcs[0].doc_id == __import__("uuid").UUID("55555555-5555-5555-5555-555555555555")
    assert calls["increments"] == 1


def test_chat_stream_quota_exceeded_no_llm(client, monkeypatch):
    tc, Local, calls = client

    class EmptyQuota:
        def left_today(self, _uid):
            return 0

        def increment(self, _uid, n=1):
            calls["increments"] += n

    monkeypatch.setattr("app.api.chat.get_quota_service", lambda: EmptyQuota())
    called = []

    async def _fake(*_a, **_k):
        called.append(1)
        yield ("token", {"delta": "x"})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "保修多久", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert "QUOTA_EXCEEDED" in r.text
    assert called == []  # 未调 LLM


def test_chat_stream_foreign_session_404(client):
    tc, *_ = client
    # 他人 session（user 不同，表中不存在）
    r = tc.post(
        f"{API}/chat/stream",
        json={
            "session_id": "99999999-9999-9999-9999-999999999999",
            "content": "保修多久",
            "stream": True,
        },
        headers=_headers(),
    )
    assert r.status_code == 404


def test_chat_stream_unauthenticated_401(client):
    tc, *_ = client
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "hi", "stream": True},
    )
    assert r.status_code == 401


def test_sessions_crud(client):
    tc, *_ = client
    # 创建
    r = tc.post(f"{API}/sessions", json={"title": "我的会话"}, headers=_headers())
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["title"] == "我的会话"
    # 列表
    r = tc.get(f"{API}/sessions", headers=_headers())
    assert r.status_code == 200
    assert any(i["session_id"] == sid for i in r.json()["items"])
    # 详情
    r = tc.get(f"{API}/sessions/{sid}", headers=_headers())
    assert r.status_code == 200 and r.json()["session_id"] == sid
