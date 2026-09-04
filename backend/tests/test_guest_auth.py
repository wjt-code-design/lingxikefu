"""匿名会话（免登录体验，D1 完整特性 2026-09-04）后端测试。

设计（立项规划批次B）：
- POST /auth/guest 签发真实 User 行（role=user、status="guest"、随机不可登录密码）
  + 普通 access/refresh JWT → Session/配额/通知全链路零改动复用；
- 防滥用：每 IP 每日限发 GUEST_ISSUE_PER_IP_PER_DAY 个；guest 低配额
  GUEST_DAILY_QUOTA_LIMIT（try_consume(guest=True)）；
- 隐私留存：purge_expired_guests 删过期 guest 行（会话 FK CASCADE 级联删）；
- 管理面隔离：/admin/users 列表与统计排除 guest。

范式对齐 test_auth.py：内存 SQLite + get_db 覆盖。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.message import Message, MessageSource
from app.models.session import Session
from app.models.ticket import Ticket
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # SQLite 默认不开 FK：purge 级联删除依赖它，显式开启贴近 PG 语义
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # messages.meta 是 JSONB + PG server_default（SQLite 无法编译）→ 建表前替换（test_chat_api 先例）
    import sqlalchemy as sa

    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Session.__table__,
            Message.__table__,
            MessageSource.__table__,
            Ticket.__table__,  # 会话详情联查 open ticket
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
    with TestClient(app) as c:
        c.Local = Local  # 供测试直查库
        yield c
    app.dependency_overrides.clear()


def _guest(client) -> TestClient:
    r = client.post(f"{API}/auth/guest")
    assert r.status_code == 201, r.text
    return r.json()


def test_guest_issue_success(client):
    """guest 端点：201 + 三字段与 AuthResp 同构；token 可用（/me 200）。"""
    data = _guest(client)
    assert data["user_id"] and data["access_token"] and data["refresh_token"]
    r = client.get(
        f"{API}/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "user"
    assert body["guest"] is True  # /me 透传 guest 标识，前端据此展示注册引导
    assert body["quota_total"] == 10  # 默认 GUEST_DAILY_QUOTA_LIMIT


def test_guest_user_row_shape(client):
    """guest 行：status=guest、role=user、无 email/phone、密码哈希随机（不可登录）。"""
    data = _guest(client)
    db = client.Local()
    try:
        u = db.get(User, uuid.UUID(data["user_id"]))
        assert u is not None
        assert u.status == "guest"
        assert u.role.value == "user"
        assert u.email is None and u.phone is None
        assert u.password_hash  # 随机串，非空但无人知晓
    finally:
        db.close()


def test_guest_cannot_login(client):
    """guest 行无 email/phone → 不存在可登录账号路径（authenticate 查无此人 401）。"""
    data = _guest(client)
    r = client.post(
        f"{API}/auth/login",
        json={"account": data["user_id"], "password": "anything123"},
    )
    assert r.status_code == 401


def test_guest_refresh_preserves_claim(client):
    """refresh 轮换后 access 仍带 guest claim：否则刷新一次即"升级"成注册配额，低配额闸失效。"""
    data = _guest(client)
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 200
    new_access = r.json()["access_token"]
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200
    assert me.json()["guest"] is True
    assert me.json()["quota_total"] == 10


def test_guest_session_and_chat_scope(client):
    """guest token 可建会话（Session FK 指向 guest user 行）。"""
    data = _guest(client)
    h = {"Authorization": f"Bearer {data['access_token']}"}
    r = client.post(f"{API}/sessions", headers=h, json={"title": "guest-try"})
    assert r.status_code == 200
    sid = r.json()["session_id"]
    # 自己的会话可查
    r2 = client.get(f"{API}/sessions/{sid}", headers=h)
    assert r2.status_code == 200
    # 他人（注册用户）不可访问 guest 会话（既有语义：非 owner 非客服 → 403）
    r3 = client.post(
        f"{API}/auth/register",
        json={"email": "norm@b.com", "password": "secret123"},
    )
    h2 = {"Authorization": f"Bearer {r3.json()['access_token']}"}
    r4 = client.get(f"{API}/sessions/{sid}", headers=h2)
    assert r4.status_code == 403


def test_guest_issue_ip_rate_limit(client, monkeypatch):
    """每 IP 每日发放上限：第 N+1 个请求 429。"""
    from app.core.config import settings

    # conftest 全局关限流（用例间隔离）；本用例验证限流闸本身，显式打开
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "GUEST_ISSUE_PER_IP_PER_DAY", 2)
    for _ in range(2):
        assert client.post(f"{API}/auth/guest").status_code == 201
    r = client.post(f"{API}/auth/guest")
    assert r.status_code == 429


def test_guest_quota_lower(client, monkeypatch):
    """guest 低配额：try_consume(guest=True) 按 GUEST_DAILY_QUOTA_LIMIT 拒绝超额。"""
    from app.core.config import settings
    from app.services.quota import QuotaService

    monkeypatch.setattr(settings, "GUEST_DAILY_QUOTA_LIMIT", 2)
    monkeypatch.setattr(settings, "DAILY_QUOTA_LIMIT", 200)
    svc = QuotaService(redis_client=None)  # conftest fakeredis 已 patch get_redis
    uid = str(uuid.uuid4())
    assert svc.try_consume(uid, 1, guest=True)[0] is True
    assert svc.try_consume(uid, 1, guest=True)[0] is True
    ok, _ = svc.try_consume(uid, 1, guest=True)
    assert ok is False
    # 普通用户不受 guest 上限影响
    uid2 = str(uuid.uuid4())
    assert svc.try_consume(uid2, 1)[0] is True


def test_admin_users_excludes_guests(client):
    """/admin/users 列表与总数排除 guest（管理面不掺体验账号）。"""
    client.post(f"{API}/auth/register", json={"email": "admin@b.com", "password": "secret123"})
    # 提权为 admin：直接改库（注册恒为 user）
    db = client.Local()
    try:
        from app.models.user import UserRole

        u = db.scalar(select(User).where(User.email == "admin@b.com"))
        u.role = UserRole.admin
        db.commit()
        admin_tok = u.id
    finally:
        db.close()
    _guest(client)  # 掺入 1 个 guest
    client.post(f"{API}/auth/register", json={"email": "norm@b.com", "password": "secret123"})
    from app.core.security import create_access_token

    h = {"Authorization": f"Bearer {create_access_token(str(admin_tok), 'admin')}"}
    r = client.get(f"{API}/admin/users", headers=h)
    assert r.status_code == 200
    accounts = [it["account"] for it in r.json()["items"]]
    assert "admin@b.com" in accounts and "norm@b.com" in accounts
    assert r.json()["total"] == 2  # guest 不计数


def test_purge_expired_guests(client):
    """过期 guest 清理：删 user 行且会话随 FK CASCADE 级联删；未过期保留。"""
    data = _guest(client)
    uid = uuid.UUID(data["user_id"])
    db = client.Local()
    try:
        s = Session(user_id=uid, title="to-purge")
        db.add(s)
        db.commit()
        sid = s.id
        # 把 created_at 拨到 31 天前
        u = db.get(User, uid)
        u.created_at = datetime.now(UTC) - timedelta(days=31)
        db.commit()
    finally:
        db.close()

    from app.services.guest_service import purge_expired_guests

    db = client.Local()
    try:
        removed = purge_expired_guests(db, days=30)
        assert removed == 1
        assert db.get(User, uid) is None
        assert db.get(Session, sid) is None  # FK CASCADE
    finally:
        db.close()


def test_purge_keeps_recent_guests(client):
    """未过期 guest 保留（days 阈值语义）。"""
    data = _guest(client)
    from app.services.guest_service import purge_expired_guests

    db = client.Local()
    try:
        assert purge_expired_guests(db, days=30) == 0
        assert db.get(User, uuid.UUID(data["user_id"])) is not None
    finally:
        db.close()
