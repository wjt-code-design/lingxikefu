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
        self.store: dict[str, int | str] = {}

    def get(self, key):
        return self.store.get(key)

    def incr(self, key, n=1):
        self.store[key] = int(self.store.get(key, 0)) + n
        return self.store[key]

    def decr(self, key, n=1):
        self.store[key] = int(self.store.get(key, 0)) - n
        return self.store[key]

    def set(self, key, value, ex=None):
        self.store[key] = value
        return True

    def delete(self, key):
        self.store.pop(key, None)
        return 1

    def expire(self, key, ttl):
        return True


def test_quota_try_consume_idempotent():
    """R2：同一 client_msg_id 幂等 —— 重试不重复扣费。"""
    qs = QuotaService(redis_client=_FakeRedis())
    uid = "u1"
    idem = "req-abc"
    allowed, used = qs.try_consume(uid, 1, idem_key=idem)
    assert allowed and used == 1
    # 同幂等键重试（断连重发）→ 放行且不重复扣
    allowed2, used2 = qs.try_consume(uid, 1, idem_key=idem)
    assert allowed2 and used2 == 1
    assert qs.used_today(uid) == 1


def test_quota_refund_rolls_back_and_clears_idem():
    """R2：失败回滚退回已扣配额，并清除幂等标记（重试可重新扣费）。"""
    qs = QuotaService(redis_client=_FakeRedis())
    uid = "u1"
    idem = "req-abc"
    qs.try_consume(uid, 1, idem_key=idem)
    assert qs.used_today(uid) == 1
    qs.refund(uid, 1, idem_key=idem)
    assert qs.used_today(uid) == 0
    # 回滚后同幂等键重试 → 重新正常扣费
    allowed, used = qs.try_consume(uid, 1, idem_key=idem)
    assert allowed and used == 1


def test_quota_refund_noop_when_not_consumed():
    """R2：未扣费（无幂等标记）时 refund 无动作，不产生负计数。"""
    qs = QuotaService(redis_client=_FakeRedis())
    qs.refund("u1", 1, idem_key="never-consumed")
    assert qs.used_today("u1") == 0
