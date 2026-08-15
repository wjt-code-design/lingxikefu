"""Quota 测试（BU-08）：端点降级 + 服务逻辑（假 Redis 注入）。"""
from __future__ import annotations

import app.models.user
import pytest
from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.user import User
from app.services.quota import QuotaService
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


def _token(client, email="q@b.com"):
    r = client.post(f"{API}/auth/register", json={"email": email, "password": "secret123"})
    return r.json()["access_token"]


def test_quota_requires_auth(client):
    assert client.get(f"{API}/quota").status_code == 401


def test_quota_degrades_when_redis_down(client):
    # 本地无 Redis → used=0, left=limit（优雅降级，不 5xx）
    tok = _token(client)
    r = client.get(f"{API}/quota", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    data = r.json()
    assert data["used"] == 0
    assert data["left"] == data["limit"] > 0


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def incr(self, key, n=1):
        self.store[key] = self.store.get(key, 0) + n
        return self.store[key]

    def expire(self, key, ttl):
        return True


def test_quota_service_increment_and_left():
    qs = QuotaService(redis_client=_FakeRedis())
    uid = "u1"
    assert qs.used_today(uid) == 0
    qs.increment(uid)
    qs.increment(uid, 2)
    assert qs.used_today(uid) == 3
    assert qs.left_today(uid) == qs.daily_limit() - 3
