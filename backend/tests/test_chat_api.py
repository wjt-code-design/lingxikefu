"""Chat API 测试（BU-06）：SSE 事件顺序 / 会话归属 / 配额 / 来源落库。

- SQLite StaticPool + get_db 覆盖；建 session/message/message_sources/kb 表；
- mock stream_answer（直接 yield 契约事件序列），不依赖真实 RAG/Qdrant/百炼；
- mock quota（避免 Redis 依赖）。
"""
from __future__ import annotations

import app.models.knowledge  # noqa: F401
import pytest
import uuid
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Document, KnowledgeBase
from app.models.message import Message, MessageRole, MessageSource
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User
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
            Ticket.__table__,  # T1：handoff 建单测试
            User.__table__,  # BUG-12：list_sessions 回填 user_email/user_phone
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

    # mock quota：余额充足，记录 try_consume 调用（M2 后消耗走原子闸门）
    calls = {"consumed": 0}

    class FakeQuota:
        def left_today(self, _uid):
            return 10

        def try_consume(self, _uid, n=1, idem_key=None):  # idem_key：与生产签名对齐（配额幂等键，2026-08-20 补）
            calls["consumed"] += n
            return (True, 0)

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
    async def __call__(query, kb_id, history=None, top_k=5, **kwargs):  # **kwargs：兼容 T10 kb_version
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
    assert calls["consumed"] == 1


def test_chat_stream_quota_exceeded_no_llm(client, monkeypatch):
    tc, Local, calls = client

    class EmptyQuota:
        def left_today(self, _uid):
            return 0

        def try_consume(self, _uid, n=1, idem_key=None):  # idem_key：与生产签名对齐（配额幂等键，2026-08-20 补）
            return (False, 0)  # 超限 → 闸门拒绝

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
    # 详情（M8：返回 SessionDetail，含 messages，id 字段）
    r = tc.get(f"{API}/sessions/{sid}", headers=_headers())
    assert r.status_code == 200 and r.json()["id"] == sid
    assert "messages" in r.json()


def test_session_ownership_agent_can_read_other_user(client):
    """R-1：agent/admin 可读任意用户会话（客服查看场景）；非所有者 user 仍 403。"""
    tc, *_ = client
    r = tc.post(f"{API}/sessions", json={"title": "用户会话"}, headers=_headers())
    sid = r.json()["session_id"]

    agent_h = {
        "Authorization": f"Bearer {create_access_token('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'agent')}"
    }
    assert tc.get(f"{API}/sessions/{sid}", headers=agent_h).status_code == 200

    admin_h = {
        "Authorization": f"Bearer {create_access_token('cccccccc-cccc-cccc-cccc-cccccccccccc', 'admin')}"
    }
    assert tc.get(f"{API}/sessions/{sid}", headers=admin_h).status_code == 200

    other_user_h = {
        "Authorization": f"Bearer {create_access_token('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'user')}"
    }
    assert tc.get(f"{API}/sessions/{sid}", headers=other_user_h).status_code == 403


def test_chat_stream_handoff_creates_ticket(client, monkeypatch):
    """T1：intent=handoff → AI 建单（幂等 + 溯源锚点 message_id），done 带 ticket_id。"""
    tc, Local, _ = client

    async def _fake(*_a, **_k):
        yield ("intent", {"intent": "handoff"})
        yield ("stage", {"stage": "retrieving"})
        yield ("token", {"delta": "已为您转接人工"})
        yield ("done", {"message_id": ""})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "我要投诉找经理", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert '"ticket_id"' in r.text  # done 事件携带工单号

    with Local() as db:
        tickets = db.scalars(select(Ticket)).all()
        assert len(tickets) == 1
        assert tickets[0].status == TicketStatus.open
        assert tickets[0].message_id is not None  # T1 溯源锚点已落
        # Bug #1 修复：user 消息 intent 已回写为 handoff（此前恒 qa → F1 hot_gaps 数据源失效）
        user_msg = db.scalars(
            select(Message).where(
                Message.role == MessageRole.user,
                Message.session_id == uuid.UUID("11111111-1111-1111-1111-111111111111"),
            )
        ).first()
        assert user_msg is not None and user_msg.intent == "handoff"


def test_agent_can_reply_on_user_session(client, monkeypatch):
    """T5：agent 代答——可对用户会话 chat/stream（记录 agent_id）。

    人工直复已迁移至 POST /sessions/{id}/messages（Branch 3，见 test_sessions_messages.py）；
    原 /chat/reply 端点已删除（前端零调用，且与新端点 role=agent 语义冲突）。
    """
    tc, Local, _ = client
    agent_h = {
        "Authorization": f"Bearer {create_access_token('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'agent')}"
    }

    # 代答流式：agent 向用户 session 发问 → 200 + user 消息带 agent_id
    async def _fake(*_a, **_k):
        yield ("intent", {"intent": "qa"})
        yield ("token", {"delta": "好的"})
        yield ("done", {"message_id": ""})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "我来帮您查询", "stream": True},
        headers=agent_h,
    )
    assert r.status_code == 200
    with Local() as db:
        agent_msg = db.scalars(select(Message).where(Message.role == MessageRole.user)).all()[-1]
        assert agent_msg.meta.get("agent_id") == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_sse_event_whitelist():
    """C2：SSE 事件名白名单——合法事件可编码，越界降级为 error 事件（不掐断流）。"""
    from app.api.chat import _sse

    for ev in ("stage", "intent", "token", "sources", "done", "error"):
        assert '"event": "%s"' % ev in _sse({"event": ev, "data": {}})
    out = _sse({"event": "typo_event", "data": {}})
    assert '"event": "error"' in out and "SSE_CONTRACT" in out  # fail-open：error 事件而非 raise


def test_sse_events_match_frontend_contract():
    """C2 闭环：后端 SSE 事件名集合 == 前端 contracts/api.ts SSEEvent union 的 event 字面量。"""
    from pathlib import Path
    import re as _re

    from app.api.chat import _SSE_EVENTS

    backend_events = set(_SSE_EVENTS)
    # 契约单一真源：frontend/src/contracts/api.ts 已是 re-export 桥（无类型字面量），
    # 直接读根 contracts/api.ts（SSEEvent union 所在处）
    contract_path = Path(__file__).resolve().parent.parent.parent / "contracts" / "api.ts"
    if not contract_path.exists():
        pytest.skip("contracts/api.ts 不存在（仅后端子目录 CI 场景）")
    text = contract_path.read_text(encoding="utf-8")
    frontend_events = set(_re.findall(r"event: '(\w+)'", text))
    assert backend_events == frontend_events, (
        f"SSE 契约漂移：后端 {sorted(backend_events)} vs 前端 {sorted(frontend_events)}"
    )


def test_session_satisfaction(client):
    """P2-2：会话满意度评分——幂等覆盖 + 非法值 422 + 越权 404 防探测。"""
    tc = client[0]
    sid = tc.post(f"{API}/sessions", json={"title": "满意度"}, headers=_headers()).json()["session_id"]
    # 正常评分 + 幂等覆盖
    assert tc.post(f"{API}/sessions/{sid}/satisfaction", json={"rating": "satisfied"}, headers=_headers()).status_code == 200
    assert tc.post(f"{API}/sessions/{sid}/satisfaction", json={"rating": "unsatisfied"}, headers=_headers()).status_code == 200
    # 非法值 → 422
    assert tc.post(f"{API}/sessions/{sid}/satisfaction", json={"rating": "meh"}, headers=_headers()).status_code == 422
    # 越权：别人的会话 → 404（防探测）
    other_sid = tc.post(f"{API}/sessions", json={}, headers={
        "Authorization": f"Bearer {create_access_token('99999999-9999-9999-9999-999999999999', 'user')}"
    }).json()["session_id"]
    assert tc.post(f"{API}/sessions/{other_sid}/satisfaction", json={"rating": "neutral"}, headers=_headers()).status_code == 404
