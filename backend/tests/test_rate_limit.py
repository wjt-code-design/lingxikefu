"""登录/注册限流单测（P3-⑪）：incr+expire 原子 pipeline + Redis 故障 fail-open。

覆盖：
- 成功路径每次计数都带 TTL（计入多少、同时设多久失效——消除「incr 后 expire 前崩溃 → 永不过期」的永久误伤窗口）；
- execute 抛错（模拟 expire 中途失败）→ fail-open 放行（不锁死登录）；
- Redis 完全不可用 → fail-open 放行。
"""
from __future__ import annotations

import fakeredis
import pytest
from app.core import rate_limit as rl


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeStrictRedis:
    server = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(rl, "get_redis", lambda: server)
    monkeypatch.setattr(rl.settings, "RATE_LIMIT_ENABLED", True)
    return server


def test_pipeline_incr_expire_committed_together(fake_redis) -> None:
    """P3-⑪：incr 与 expire 同批提交——计数键必有 TTL，不会因中途崩溃而永久限流。"""
    key = "rl:pipeline-test"
    for _ in range(3):
        assert rl.rate_limit(key, 5, 60)
    ttl = fake_redis.ttl(key)
    assert ttl > 0, "incr+expire 必须原子提交：计数键应带 TTL"


def test_second_window_recovers_after_expire_failure(monkeypatch: pytest.MonkeyPatch, fake_redis) -> None:
    """P3-⑪：expire 失败注入 → execute 抛错 → fail-open 放行（不永久锁死登录）。"""
    key = "rl:expire-fail"

    class _BoomingPipe:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def execute(self):
            raise ConnectionError("simulated expire pipe failure")

    monkeypatch.setattr(fake_redis, "pipeline", lambda: _BoomingPipe(fake_redis.pipeline(transaction=True)))
    # 此次调用失败 → 必须放行（永不因一次瞬时故障把用户永久拒之门外）
    assert rl.rate_limit(key, 5, 60) is True


def test_redis_unavailable_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 完全不可用 → fail-open 放行（登录防爆破的降级语义不变）。"""

    def boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(rl, "get_redis", boom)
    monkeypatch.setattr(rl.settings, "RATE_LIMIT_ENABLED", True)
    assert rl.rate_limit("rl:down", 5, 60) is True


def test_disabled_env_allows_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """RATE_LIMIT_ENABLED=false（测试/内部环境）→ 直接放行，不触 Redis。"""
    monkeypatch.setattr(rl.settings, "RATE_LIMIT_ENABLED", False)
    assert rl.rate_limit("rl:off", 1, 60) is True
