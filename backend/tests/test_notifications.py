"""通知中心（SSE）API 测试：/notifications 列表/已读 + 事件源埋点 + 权限隔离。

- 数据库范式对齐 test_tickets.py：内存 SQLite + dependency_overrides[get_db]
  （不连真实 PG，虚构 uuid 建会话不受 FK 约束）；仅建所需表 sessions/tickets/notifications。
- SSE：HTTP 层只验证握手响应头（TestClient 读无限流可能缓冲挂起）；
  事件序列（connected→ping）直接消费 _sse_gen 生成器（asyncio.run + wait_for 超时保护）。
"""
from __future__ import annotations

import asyncio
import queue
import uuid

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.notification import Notification
from app.models.session import Session
from app.models.ticket import Ticket
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
AGENT = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
USER = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _h(uid: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(uid), role=role)}"}


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[Session.__table__, Ticket.__table__, Notification.__table__],
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _create_session(client, token: dict, title: str = "notify-test") -> str:
    r = client.post(f"{API}/sessions", headers=token, json={"title": title})
    assert r.status_code == 200
    return r.json()["session_id"]


def _unread(client, token: dict) -> int:
    r = client.get(f"{API}/notifications/unread-count", headers=token)
    assert r.status_code == 200
    return r.json()["count"]


def test_user_forbidden(client):
    """user 无通知中心：列表/未读/stream 均 403。"""
    for path in ("/notifications", "/notifications/unread-count"):
        r = client.get(f"{API}{path}", headers=_h(USER, "user"))
        assert r.status_code == 403
    r = client.get(f"{API}/notifications/stream", headers=_h(USER, "user"))
    assert r.status_code == 403


def test_create_ticket_notifies_agent(client):
    """agent 手动建单 → agent 收到 ticket.created + 未读+1；admin 按角色隔离看不到。"""
    token = _h(AGENT, "agent")
    before = _unread(client, token)
    sid = _create_session(client, token)
    r = client.post(f"{API}/tickets", headers=token, json={"session_id": sid})
    assert r.status_code == 201
    lst = client.get(f"{API}/notifications", headers=token, params={"size": 50}).json()
    assert any(it["event_type"] == "ticket.created" for it in lst["items"])
    assert _unread(client, token) >= before + 1
    # 角色隔离：agent 角色的通知，admin 列表不应出现
    admin_lst = client.get(
        f"{API}/notifications", headers=_h(ADMIN, "admin"), params={"size": 50}
    ).json()
    assert all(it["event_type"] != "ticket.created" for it in admin_lst["items"])


def test_escalate_notifies_agent(client):
    """user 主动转人工 → agent 收到 ticket.transfer 通知（且不产生 ticket.created 双通知）。"""
    token = _h(AGENT, "agent")
    usr = _h(USER, "user")
    sid = _create_session(client, usr, "escalate-test")
    r = client.post(f"{API}/tickets/escalate/{sid}", headers=usr)
    assert r.status_code == 201
    lst = client.get(f"{API}/notifications", headers=token, params={"size": 50}).json()
    assert any(it["event_type"] == "ticket.transfer" for it in lst["items"])
    # 手动转人工不应额外触发 ticket.created（ensure_active_ticket notify=False）
    assert not any(it["event_type"] == "ticket.created" for it in lst["items"])


def test_satisfaction_notifies_admin(client):
    """user 提交满意度 → admin 收到 satisfaction.submitted 通知。"""
    token = _h(ADMIN, "admin")
    usr = _h(USER, "user")
    sid = _create_session(client, usr, "sat-test")
    r = client.post(f"{API}/sessions/{sid}/satisfaction", headers=usr, json={"rating": "satisfied"})
    assert r.status_code == 200
    lst = client.get(f"{API}/notifications", headers=token, params={"size": 50}).json()
    assert any(it["event_type"] == "satisfaction.submitted" for it in lst["items"])


def test_mark_read_decreases_unread(client):
    """单条已读：未读数精确减一。"""
    token = _h(AGENT, "agent")
    sid = _create_session(client, token)
    client.post(f"{API}/tickets", headers=token, json={"session_id": sid})
    lst = client.get(f"{API}/notifications", headers=token, params={"size": 50}).json()
    unread_items = [it for it in lst["items"] if not it["is_read"]]
    assert unread_items
    before = _unread(client, token)
    target = unread_items[0]["notification_id"]
    r = client.post(f"{API}/notifications/{target}/read", headers=token)
    assert r.status_code == 200
    assert _unread(client, token) == before - 1
    # 已读后再次标记仍 200（幂等）
    r2 = client.post(f"{API}/notifications/{target}/read", headers=token)
    assert r2.status_code == 200


def test_read_all_clears(client):
    """全部已读：本角色未读归零。"""
    token = _h(AGENT, "agent")
    sid = _create_session(client, token)
    client.post(f"{API}/tickets", headers=token, json={"session_id": sid})
    r = client.post(f"{API}/notifications/read-all", headers=token)
    assert r.status_code == 200
    assert _unread(client, token) == 0


def test_stream_headers_200():
    """SSE 端点信封：返回 200 + text/event-stream。

    直接调用端点函数验证响应对象（httpx ASGITransport 对无限流不传播断开会挂起，
    故不在 HTTP 层消费流；事件序列语义由 test_sse_stream_events 覆盖）。
    """
    from app.api.notifications import stream_notifications

    resp = asyncio.run(stream_notifications(payload={"sub": str(AGENT), "role": "agent"}))
    assert resp.status_code == 200
    assert resp.media_type == "text/event-stream"


def test_sse_stream_events(monkeypatch):
    """SSE 事件序列：connected 握手 → 心跳 ping（队列空兜底），wait_for 超时保护防挂起。"""
    import app.api.notifications as mod

    def _empty_get(timeout=None):
        raise queue.Empty

    monkeypatch.setattr(mod._notify_queue, "get", _empty_get)
    frames: list[str] = []

    async def _probe():
        gen = mod._sse_gen("agent")
        for _ in range(3):
            frames.append(await gen.__anext__())

    asyncio.run(asyncio.wait_for(_probe(), timeout=5))
    assert any("connected" in f for f in frames)
    assert any("ping" in f for f in frames)
