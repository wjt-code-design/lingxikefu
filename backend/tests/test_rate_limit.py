"""登录/注册限流单测（P3-⑪ / B3 / A1）：固定窗口原子计数 + 超限真拦 + Redis 故障 fail-open。

覆盖：
- 超过 limit 的请求**返回 False**（A1：这条是限流器唯一的行为尺子，改成永不拦截必红）；
- 被拒请求不刷新窗口 TTL（B3：否则每 <window 秒重试一次就永不解锁）；
- 成功路径每次计数都带 TTL（计数键与 TTL 同批提交——消除「建键后 expire 前崩溃 → 永不过期」的永久误伤窗口）；
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


def test_requests_over_limit_are_blocked(fake_redis) -> None:
    """A1（审计 2026-09-27）：limit=2 的三连击，第 3 次必须 False（真拦，不是只断键有没有 TTL）。

    补这条前，全仓对 rate_limit 的断言只有 `assert rl.rate_limit(...)`（恒真）——把实现改成
    永不拦截，792 条测试逐字照绿（审计变异已证实）。期望值写死为字面量，不从实现里取常量。
    """
    key = "rl:block-me"
    assert rl.rate_limit(key, 2, 60) is True
    assert rl.rate_limit(key, 2, 60) is True
    assert rl.rate_limit(key, 2, 60) is False, "第 3 次超出 limit=2，必须被拒"
    assert rl.rate_limit(key, 2, 60) is False, "窗口未过期的后续请求同样被拒"


def test_denied_request_does_not_reset_window(fake_redis) -> None:
    """B3（审计 2026-09-27）：被拒请求不得把固定窗口重置回满值。

    否则客户端只要每 <window 秒重试一次，封锁永不自动解除——共享出口 IP（公司 NAT/校园网）
    下一个人撞满配额会把同 IP 其他人长期锁死在登录页。手法：手动把键 TTL 压到 10s（窗口已过大半），
    再打一次必被拒的请求，TTL 必须仍是 ~10s 而不是被刷回 60s。
    """
    key = "rl:half-window"
    fake_redis.set(key, "2", ex=60)  # 已达 limit=2 ⇒ 下一次必被拒
    assert rl.rate_limit(key, 2, 60) is False
    fake_redis.expire(key, 10)  # 模拟窗口只剩最后 10s
    assert rl.rate_limit(key, 2, 60) is False
    ttl = fake_redis.ttl(key)
    assert 0 < ttl <= 11, f"被拒请求把窗口重置回了 {ttl}s ⇒ 只要持续重试就永不解锁"


def test_pipeline_incr_expire_committed_together(fake_redis) -> None:
    """P3-⑪：计数与 TTL 同批提交——计数键必有 TTL，不会因中途崩溃而永久限流。"""
    key = "rl:pipeline-test"
    for _ in range(3):
        assert rl.rate_limit(key, 5, 60)
    ttl = fake_redis.ttl(key)
    assert ttl > 0, "计数与 TTL 必须原子提交：计数键应带 TTL"


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
