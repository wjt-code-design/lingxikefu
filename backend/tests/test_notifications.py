"""通知中心（SSE）API 测试：/notifications 列表/已读 + 事件源埋点 + 权限隔离。

- 数据库范式对齐 test_tickets.py：内存 SQLite + dependency_overrides[get_db]
  （不连真实 PG，虚构 uuid 建会话不受 FK 约束）；仅建所需表 sessions/tickets/notifications。
- SSE：HTTP 层只验证握手响应头（TestClient 读无限流可能缓冲挂起）；
  事件序列（connected→ping）直接消费 _sse_gen 生成器（asyncio.run + wait_for 超时保护）。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.notification import Notification
from app.models.session import Session
from app.models.ticket import Ticket
from app.services.notification_service import create_notification
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


def test_user_can_access_own_directed_notifications(client):
    """user 通知中心按人投递：列表/未读只含 recipient_user_id=本人 的定向通知。"""
    # 直接经服务层写入定向通知（绕开 HTTP，聚焦查询/权限语义）：
    # fixture 的 Local sessionmaker 未导出——复用 dependency_overrides 生成器取会话
    gen = app.dependency_overrides[get_db]()
    db = next(iter(gen))
    try:
        create_notification(
            db,
            recipient_role="user",
            event_type="ticket.status_changed",
            title="工单已受理",
            content="客服已开始处理你的问题",
            resource_type="ticket",
            resource_id=str(uuid.uuid4()),
            recipient_user_id=str(USER),
        )
        # 另一用户的定向通知（本人不应看到）
        other = uuid.uuid4()
        create_notification(
            db,
            recipient_role="user",
            event_type="ticket.status_changed",
            title="别人的通知",
            recipient_user_id=str(other),
        )
        # 角色广播通知（recipient_user_id=NULL，user 也不应看到——user 只见定向）
        create_notification(
            db,
            recipient_role="user",
            event_type="ticket.status_changed",
            title="广播通知（无定向）",
        )
    finally:
        db.close()

    token = _h(USER, "user")
    lst = client.get(f"{API}/notifications", headers=token, params={"size": 50}).json()
    titles = [it["title"] for it in lst["items"]]
    assert "工单已受理" in titles
    assert "别人的通知" not in titles
    assert "广播通知（无定向）" not in titles
    assert _unread(client, token) == 1
    # 单条已读：定向通知可标记
    nid = lst["items"][0]["notification_id"]
    r = client.post(f"{API}/notifications/{nid}/read", headers=token)
    assert r.status_code == 200
    assert _unread(client, token) == 0
    # 越权：user 标记他人通知 → 404
    r2 = client.post(f"{API}/notifications/{uuid.uuid4()}/read", headers=token)
    assert r2.status_code == 404


def test_agent_role_still_sees_broadcast(client):
    """agent/admin 兼容旧语义：角色广播（recipient_user_id=NULL）仍按角色可见。"""
    token = _h(AGENT, "agent")
    sid = _create_session(client, token)
    client.post(f"{API}/tickets", headers=token, json={"session_id": sid})
    assert any(
        it["event_type"] == "ticket.created"
        for it in client.get(
            f"{API}/notifications", headers=token, params={"size": 50}
        ).json()["items"]
    )


def test_agent_sees_directed_notification_to_self(client):
    """agent 定向通知：recipient_user_id=本人 时列表可见（按人投递对 agent 同样生效）。"""
    gen = app.dependency_overrides[get_db]()
    db = next(iter(gen))
    try:
        create_notification(
            db,
            recipient_role="agent",
            event_type="ticket.assigned",
            title="指派给你",
            recipient_user_id=str(AGENT),
        )
    finally:
        db.close()
    lst = client.get(
        f"{API}/notifications", headers=_h(AGENT, "agent"), params={"size": 50}
    ).json()
    assert any(it["title"] == "指派给你" for it in lst["items"])


def test_ticket_status_change_notifies_session_owner(client):
    """工单流转 →processing/resolved 时定向回推会话属主（user）。"""
    usr = _h(USER, "user")
    agent = _h(AGENT, "agent")
    sid = _create_session(client, usr, "lifecycle-test")
    r = client.post(f"{API}/tickets/escalate/{sid}", headers=usr)
    assert r.status_code == 201
    tid = r.json()["ticket_id"]
    t = r.json()["version"]
    # agent 受理：open → processing
    r2 = client.patch(
        f"{API}/tickets/{tid}", headers=agent, json={"status": "processing", "version": t}
    )
    assert r2.status_code == 200
    # user 应收到定向通知
    lst = client.get(f"{API}/notifications", headers=usr, params={"size": 50}).json()
    assert any(it["event_type"] == "ticket.status_changed" for it in lst["items"])
    # 属主未读 ≥1
    assert _unread(client, usr) >= 1


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
    """SSE 事件序列：connected 握手 → 心跳 ping（无通知时兜底），wait_for 超时保护防挂起。"""
    import app.api.notifications as mod

    # M1 重写后无共享队列可注入：把心跳间隔缩到极短，让 ping 立即产生
    monkeypatch.setattr(mod, "_HEARTBEAT_INTERVAL", 0.05)
    frames: list[str] = []

    async def _probe():
        gen = mod._sse_gen("agent")
        for _ in range(2):
            frames.append(await gen.__anext__())

    asyncio.run(asyncio.wait_for(_probe(), timeout=5))
    assert any("connected" in f for f in frames)
    assert any("ping" in f for f in frames)


def test_same_role_subscribers_both_receive_notification():
    """M1（外部审查 2026-08-22）：同角色两个 SSE 连接（双开标签页）必须都收到同一条通知。

    旧实现是单队列抢占式消费：一条通知被其中一个连接 get 走、角色不匹配即丢弃——
    同角色的另一个连接永远收不到，实时推送静默失效。改为每连接独立队列 + 按角色
    广播后本测试才可能通过。"""

    async def scenario():
        import app.api.notifications as nmod
        import app.services.notification_service as svc

        g1 = nmod._sse_gen("agent")
        g2 = nmod._sse_gen("agent")
        await asyncio.wait_for(g1.__anext__(), 5)  # connected
        await asyncio.wait_for(g2.__anext__(), 5)  # connected

        n = Notification(recipient_role="agent", event_type="ticket.transfer", title="t")
        svc._enqueue(n)  # 与 create_notification 同一发布入口

        got1 = await asyncio.wait_for(g1.__anext__(), 5)  # 旧实现：g1 抢走或 g2 抢走，另一个超时
        got2 = await asyncio.wait_for(g2.__anext__(), 5)
        assert '"notification"' in got1
        assert '"notification"' in got2

    asyncio.run(asyncio.wait_for(scenario(), timeout=10))
