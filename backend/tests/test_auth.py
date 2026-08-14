"""Auth 端点测试（BU-02）：SQLite 内存库 + get_db 覆盖，无需 PostgreSQL。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.user  # 注册 User 表到 Base.metadata
from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.user import User

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
    r = client.post(f"{API}/auth/register", json={"password": "x"})
    assert r.status_code == 400


def test_register_duplicate_email(client):
    _register(client, email="dup@b.com")
    r = _register(client, email="dup@b.com")
    assert r.status_code == 400


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
