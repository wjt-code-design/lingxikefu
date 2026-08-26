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


class _FakePipeline:
    """模拟 redis-py Pipeline(transaction=True)：命令入队，execute() 批量应用。"""

    def __init__(self, redis: _FakeRedis):
        self._redis = redis
        self._cmds: list[tuple] = []

    def incr(self, key, n=1):
        self._cmds.append(("incr", key, n))
        return self

    def expire(self, key, ttl):
        self._cmds.append(("expire", key, ttl))
        return self

    def set(self, key, value, ex=None):
        self._cmds.append(("set", key, value, ex))
        return self

    def delete(self, key):
        self._cmds.append(("delete", key))
        return self

    def execute(self):
        results = []
        for cmd in self._cmds:
            op, key = cmd[0], cmd[1]
            if op == "incr":
                results.append(self._redis.incr(key, cmd[2]))
            elif op == "expire":
                results.append(self._redis.expire(key, cmd[2]))
            elif op == "set":
                results.append(self._redis.set(key, cmd[2], ex=cmd[3]))
            elif op == "delete":
                results.append(self._redis.delete(key))
        return results


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, int | str] = {}

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

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


class _NoPipelineRedis(_FakeRedis):
    """pipeline 不可用（MULTI 建立即失败）——try_consume 必须 fail-closed 且无脏状态。"""

    def pipeline(self, transaction=True):
        raise ConnectionError("redis pipeline unavailable")


def test_quota_try_consume_idempotent():
    """R2/P1-①：同用户同 client_msg_id 同 content 重试 —— 不重复扣费。"""
    qs = QuotaService(redis_client=_FakeRedis())
    uid = "u1"
    idem = "req-abc"
    allowed, used = qs.try_consume(uid, 1, idem_key=idem, content="你好")
    assert allowed and used == 1
    # 同幂等键同内容重试（断连重发）→ 放行且不重复扣
    allowed2, used2 = qs.try_consume(uid, 1, idem_key=idem, content="你好")
    assert allowed2 and used2 == 1
    assert qs.used_today(uid) == 1


def test_quota_idem_marker_not_shared_across_users():
    """P1-①：幂等标记绑定用户 —— 用户 B 复用用户 A 的 client_msg_id 必须正常扣费。"""
    qs = QuotaService(redis_client=_FakeRedis())
    allowed_a, _ = qs.try_consume("alice", 1, idem_key="req-shared")
    assert allowed_a
    # 裸 marker 红态：B 直接命中 A 的标记 → (True, 0)，免费蹭掉本应扣除的一次配额
    allowed_b, used_b = qs.try_consume("bob", 1, idem_key="req-shared")
    assert allowed_b and used_b == 1
    assert qs.used_today("bob") == 1


def test_quota_idem_same_user_diff_content_charged():
    """P1-①：幂等标记绑定内容指纹 —— 同用户同 client_msg_id 不同 content = 新请求正常扣费。"""
    qs = QuotaService(redis_client=_FakeRedis())
    uid, idem = "u1", "req-abc"
    allowed1, _ = qs.try_consume(uid, 1, idem_key=idem, content="第一个问题")
    assert allowed1
    # 裸 marker 红态：换了内容仍命中旧标记 → 第二次被免费放行
    allowed2, used2 = qs.try_consume(uid, 1, idem_key=idem, content="第二个问题")
    assert allowed2 and used2 == 2
    assert qs.used_today(uid) == 2


def test_quota_refund_with_content_rolls_back():
    """P1-①：refund 与 try_consume 定位同一枚指纹标记 —— 带 content 的消费可回滚。"""
    qs = QuotaService(redis_client=_FakeRedis())
    qs.try_consume("u1", 1, idem_key="req-c", content="退货政策是什么")
    assert qs.used_today("u1") == 1
    # refund 入参与扣费一致（含 content）→ 命中同一指纹标记 → 回滚成功
    qs.refund("u1", 1, idem_key="req-c", content="退货政策是什么")
    assert qs.used_today("u1") == 0


def test_quota_fail_closed_when_pipeline_unavailable():
    """P1-①：incr/expire/set(marker) 已并入 MULTI —— pipeline 不可用时 fail-closed 且无半步脏状态。"""
    qs = QuotaService(redis_client=_NoPipelineRedis())
    allowed, used = qs.try_consume("u1", 1)
    assert allowed is False and used == 0
    assert qs.used_today("u1") == 0


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
