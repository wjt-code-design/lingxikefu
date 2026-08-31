"""Quota 测试（BU-08）：端点降级 + 服务逻辑（假 Redis 注入）。"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

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

    def set(self, key, value, ex=None, nx=False):
        if nx and self._redis.store and key in self._redis.store:
            return None
        self._cmds.append(("set", key, value, ex, nx))
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
                results.append(self._redis.set(key, cmd[2], ex=cmd[3], nx=cmd[4]))
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
        # M8 红测窗口制造：sleep 释放 GIL，放大旧实现 get(marker)→set(marker)
        # 的竞态窗口（新实现 marker 抢占为 SET NX 单命令，仅单线程 incr，不受影响）。
        time.sleep(0.02)
        self.store[key] = int(self.store.get(key, 0)) + n
        return self.store[key]

    def decr(self, key, n=1):
        self.store[key] = int(self.store.get(key, 0)) - n
        return self.store[key]

    def set(self, key, value, ex=None, nx=False):
        # nx 语义（M8）：key 已存在时不覆盖并返回 None（falsy）——与 redis-py 一致。
        # nx 判断与写入在同一同步块（GIL 下无 yield 点 = 原子），模拟 SET NX 单命令语义。
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def delete(self, key):
        self.store.pop(key, None)
        return 1

    def expire(self, key, ttl):
        return True

    def eval(self, script, numkeys, *keys_and_args):
        # 仅模拟 quota._REFUND_LUA 语义：KEYS[1]=marker KEYS[2]=counter
        # ARGV[1]=expected_token ARGV[2]=n —— 原子「GET 校验归属 → DECRBY → DEL」。
        from app.services.quota import _REFUND_LUA

        if script != _REFUND_LUA:
            raise NotImplementedError("FakeRedis.eval 仅支持 quota._REFUND_LUA")
        marker, counter = keys_and_args[0], keys_and_args[1]
        expected, n = keys_and_args[2], int(keys_and_args[3])
        if self.store.get(marker) == expected:
            self.store[counter] = int(self.store.get(counter, 0)) - n
            self.store.pop(marker, None)
            return 1
        return 0


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


def test_quota_concurrent_same_idem_single_charge():
    """M8（bughunt-concurrency Major-8）：marker check-then-set 竞态 —— 并发同指纹请求只扣一次费。

    旧实现 get(marker) 与 set(marker) 两步分离：并发窗口内 N 个请求都看到
    marker=None → 全部走 incr 扣费（同一 client_msg_id 重试被重复扣）。
    修复后幂等抢占必须为 SET NX EX 单命令原子语义。

    barrier 对齐 10 线程起跑线 + incr sleep 放大窗口；多轮重复压低假绿概率。
    断言：最终计数恰为 1（只有抢到 marker 的那个请求真正扣费）。
    """
    uid, idem = "u1", "req-race"
    for rnd in range(3):
        qs = QuotaService(redis_client=_FakeRedis())
        barrier = threading.Barrier(10)

        def _fire():
            barrier.wait(timeout=10)
            return qs.try_consume(uid, 1, idem_key=idem)

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: _fire(), range(10)))

        assert all(a for a, _ in results), f"round {rnd}: 存在请求被拒绝 {results}"
        used = qs.used_today(uid)
        assert used == 1, f"round {rnd}: 并发同幂等键被扣了 {used} 次（check-then-set 竞态双扣费）"


def test_quota_refund_rejects_wrong_token():
    """M8 收尾：refund 必须校验 marker 归属（token）——错误 token 不得退他人费。

    竞态（bughunt M8「免费放行」链的退款侧）：同 client_msg_id 并发请求
    A（SET NX 抢到锁并扣费）/ B（NX 抢不到 → 幂等命中放行，未扣费）。
    B 失败退款时，旧实现只看 marker 存在即 DECR+DEL——退掉 A 的钱并删 A 的锁；
    A 随后 refund 无动作（marker 已删）→ 净双错：A 白扣 + B 免费。

    修复：try_consume 抢占时 marker 值携带请求级 token，refund 用 Lua 原子
    「GET 校验归属 → DECRBY → DEL」，token 不匹配即无动作。
    """
    qs = QuotaService(redis_client=_FakeRedis())
    uid, idem, content = "u1", "req-tok", "同一问题"
    ok_a, _ = qs.try_consume(uid, 1, idem_key=idem, content=content, token="tok-A")
    assert ok_a
    # B 同指纹并发到达：NX 抢不到 → 幂等命中放行（未扣费）
    ok_b, _ = qs.try_consume(uid, 1, idem_key=idem, content=content, token="tok-B")
    assert ok_b
    assert qs.used_today(uid) == 1
    # B 失败退款：token 不匹配持锁者 A → 必须无动作（不退费、不删 A 的锁）
    qs.refund(uid, 1, idem_key=idem, content=content, token="tok-B")
    assert qs.used_today(uid) == 1, "错误 token 的 refund 退掉了持锁者 A 的配额（归属未校验）"
    # A 的正常退款仍有效（marker 未被 B 误删），退后同指纹重试可重新扣费
    qs.refund(uid, 1, idem_key=idem, content=content, token="tok-A")
    assert qs.used_today(uid) == 0
    ok_c, used_c = qs.try_consume(uid, 1, idem_key=idem, content=content, token="tok-C")
    assert ok_c and used_c == 1
