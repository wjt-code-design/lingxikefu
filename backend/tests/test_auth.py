"""Auth 端点测试（BU-02）：SQLite 内存库 + get_db 覆盖，无需 PostgreSQL。"""
from __future__ import annotations

import app.models.user  # 注册 User 表到 Base.metadata
import pytest
from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
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
    Base.metadata.create_all(engine, tables=[User.__table__])
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


def _register(c, **kwargs):
    body = {"email": "a@b.com", "password": "secret123", **kwargs}
    return c.post(f"{API}/auth/register", json=body)


def test_register_success(client):
    r = _register(client)
    assert r.status_code == 201
    data = r.json()
    assert data["user_id"]
    assert data["access_token"] and data["refresh_token"]


def test_register_requires_contact(client):
    r = client.post(f"{API}/auth/register", json={"password": "secret123"})
    assert r.status_code == 400


def test_register_duplicate_email(client):
    _register(client, email="dup@b.com")
    r = _register(client, email="dup@b.com")
    assert r.status_code == 400


def test_register_ignores_legacy_role_field(client):
    """提权漏洞回归（P4 契约去误导）：注册不再声明 role 字段。

    旧契约声明 role 再 400 拒绝——攻击面仍在（注入即被接受后拒绝）；
    新契约不声明该字段：客户端即便携带 role 也被 pydantic 忽略，注册恒为 user。
    安全属性保持：不存在任何让注册账户成为 admin/agent 的路径。
    """
    r = client.post(
        f"{API}/auth/register",
        json={"email": "hack@b.com", "password": "secret123", "role": "admin"},
    )
    assert r.status_code == 201
    reg = r.json()
    me = client.get(
        f"{API}/auth/me", headers={"Authorization": f"Bearer {reg['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["role"] == "user"


def test_register_short_password_rejected(client):
    """密码强度：后端拒绝少于 6 位。"""
    r = client.post(f"{API}/auth/register", json={"email": "pw@b.com", "password": "12345"})
    assert r.status_code == 422


def test_login_success(client):
    _register(client, email="login@b.com")
    r = client.post(f"{API}/auth/login", json={"account": "login@b.com", "password": "secret123"})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_login_wrong_password(client):
    _register(client, email="wp@b.com")
    r = client.post(f"{API}/auth/login", json={"account": "wp@b.com", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_account(client):
    r = client.post(f"{API}/auth/login", json={"account": "nobody@b.com", "password": "x"})
    assert r.status_code == 401


def test_refresh_issues_new_access(client):
    reg = _register(client, email="rf@b.com").json()
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": reg["refresh_token"]})
    assert r.status_code == 200
    assert r.json()["access_token"]
    # R-4 轮换：返回新 refresh，且旧 refresh 再次使用应失效
    assert r.json()["refresh_token"]
    r2 = client.post(f"{API}/auth/refresh", json={"refresh_token": reg["refresh_token"]})
    assert r2.status_code == 401


def test_me_requires_auth(client):
    assert client.get(f"{API}/auth/me").status_code == 401


def test_me_returns_profile_and_quota(client):
    reg = _register(client, email="me@b.com").json()
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {reg['access_token']}"})
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == "me@b.com"
    assert data["role"] == "user"
    assert isinstance(data["quota_left"], int)


def test_consume_token_atomic_single_use(monkeypatch):
    """Bug #2 修复：同一 jti 并发复用 refresh token —— SETNX 原子占用，仅首次成功。"""
    from app.core import token_revocation

    class _FakeRedis:
        def __init__(self):
            self._data: dict[str, str] = {}

        def set(self, key, value, ex=None, nx=False):
            if nx and key in self._data:
                return False
            self._data[key] = value
            return True

    _fake = _FakeRedis()
    monkeypatch.setattr(token_revocation, "get_redis", lambda: _fake)  # 单例：状态跨调用共享
    assert token_revocation.consume_token("jti-1", 9999999999) is True   # 首次占用成功
    assert token_revocation.consume_token("jti-1", 9999999999) is False  # 复用被拒（防双签发）
    assert token_revocation.consume_token("jti-2", 9999999999) is True   # 不同 jti 不受影响
