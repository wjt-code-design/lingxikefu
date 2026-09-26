"""Telemetry API 测试（ErrorBoundary TODO 落地）：前端错误上报端点（纯日志，无 DB）。"""
from __future__ import annotations

import logging

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_report_frontend_error_ok(client, caplog):
    """POST /telemetry/frontend-error → 204，且写结构化日志。"""
    with caplog.at_level(logging.ERROR, logger="lingxi"):
        r = client.post(
            "/api/v1/telemetry/frontend-error",
            json={
                "message": "boom",
                "stack": "at Foo (foo.tsx:1)",
                "component": "ChatContainer",
                "url": "http://localhost/chat",
                "user_agent": "vitest",
            },
        )
    assert r.status_code == 204
    assert any("frontend_error" in rec.message and "boom" in rec.message for rec in caplog.records)


def test_report_frontend_error_empty_body(client):
    """空 body 字段默认空串，仍 204（前端可能只传部分字段）。"""
    r = client.post("/api/v1/telemetry/frontend-error", json={})
    assert r.status_code == 204


def test_report_frontend_error_log_injection_escaped(client, caplog):
    """message 含换行/控制字符 → JSON 编码后记录，不污染日志行（防日志伪造）。"""
    import json as _json

    evil = '正常日志\n2026-01-01 伪造审计条目 [INFO] hacked'
    with caplog.at_level(logging.ERROR, logger="lingxi"):
        r = client.post(
            "/api/v1/telemetry/frontend-error",
            json={"message": evil, "stack": ""},
        )
    assert r.status_code == 204
    rec = next(rec for rec in caplog.records if "frontend_error" in rec.message)
    # message 以 JSON 字符串形式记录（\n 转义为 \\n），原始换行不直接出现在日志里
    assert _json.loads(rec.message.split("message=")[1].split(" stack=")[0]) == evil
    assert "\n" not in rec.message.split("message=")[1].split(" stack=")[0]


def test_report_frontend_error_rate_limited(client, caplog, monkeypatch):
    """同 IP 超过窗口上限（60s/10 条）→ 静默 204 且不再写日志（防刷屏）。

    monkeypatch get_redis 抛异常强制走内存降级路径，保证测试不依赖 Redis 状态。
    """
    from app.api import telemetry

    def _redis_down(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(telemetry, "get_redis", _redis_down)

    telemetry._recent.clear()  # 重置内存滑动窗口，避免前序测试消耗配额
    with caplog.at_level(logging.ERROR, logger="lingxi"):
        for _ in range(13):
            r = client.post("/api/v1/telemetry/frontend-error", json={"message": "x"})
            assert r.status_code == 204
    n = sum(1 for rec in caplog.records if "frontend_error" in rec.message)
    # A8（审计 2026-09-27）：期望值写死字面量。此前是 `== telemetry._RATE_LIMIT_MAX`——
    # 期望值从被测实现里取，实现被改成 3 条或 3000 条都一起错、测试一起绿。
    assert n == 10  # 超限部分被丢弃，不写日志（13 发只记 10 条）


def test_report_frontend_error_rate_limited_via_redis(client, caplog, monkeypatch):
    """B4/A8（审计 2026-09-27）：Redis 正常路径的限流尺子（此前该分支零覆盖）。

    上面那条把 get_redis 打成抛错，只走内存降级；Redis 分支的计数与 TTL 提交（含 B4 的
    原子性缺陷）从来没有测试到达过。这里自带一个干净的 fakeredis（不用 conftest 的 session
    级实例——同 IP 键已被前序用例计数过），断三件事：
    ① 第 11 条起被静默丢弃（日志条数停在 10）；
    ② 计数键带正 TTL（不会留下永不过期的键把该 IP 永久挡在上报之外）；
    ③ 被拒请求不把固定窗口顶回满值（B3 同款语义在本端点同样成立）。
    """
    import fakeredis
    from app.api import telemetry

    server = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(telemetry, "get_redis", lambda: server)
    key = telemetry._RATE_LIMIT_KEY_PREFIX + "testclient"  # TestClient 的对端地址

    with caplog.at_level(logging.ERROR, logger="lingxi"):
        for _ in range(13):
            r = client.post("/api/v1/telemetry/frontend-error", json={"message": "x"})
            assert r.status_code == 204
    n = sum(1 for rec in caplog.records if "frontend_error" in rec.message)
    assert n == 10, "Redis 路径：每 IP 60s 内只记 10 条，第 11 条起静默丢弃"

    ttl = server.ttl(key)
    assert 0 < ttl <= 60, f"计数键必须带正 TTL（否则残留键会永久挡住该 IP 上报），实得 {ttl}"

    server.expire(key, 10)  # 模拟窗口已过大半
    client.post("/api/v1/telemetry/frontend-error", json={"message": "x"})
    assert server.ttl(key) <= 11, "被拒请求重置了窗口 ⇒ 只要持续重试就永不解锁"


def test_limited_commits_counter_and_ttl_in_one_execute(monkeypatch):
    """B4 的原子性尺子：建键（带 TTL）与计数必须在**同一次 execute** 里提交。

    崩溃窗口无法用黑盒行为复现（只能在 incr 与 expire 之间真被杀），所以这里断的是
    「一条命令一条命令分两次发」这种形态本身：老写法 `count = r.incr(key)` +
    `if count == 1: r.expire(key, ...)` 根本不走 pipeline ⇒ 断言直接红。
    与 rate_limit.py 的 P3-⑪ 同源（那里已因同类缺陷修过一次，本端点当时漏改）。
    """
    import fakeredis
    from app.api import telemetry

    executed: list[tuple[str, ...]] = []

    class _SpyPipe:
        def __init__(self, inner):
            self._inner = inner
            self._queued: list[str] = []

        def set(self, *args, **kwargs):
            self._queued.append("set")
            return self._inner.set(*args, **kwargs)

        def incr(self, *args, **kwargs):
            self._queued.append("incr")
            return self._inner.incr(*args, **kwargs)

        def expire(self, *args, **kwargs):
            self._queued.append("expire")
            return self._inner.expire(*args, **kwargs)

        def execute(self):
            executed.append(tuple(self._queued))
            return self._inner.execute()

    server = fakeredis.FakeStrictRedis(decode_responses=True)

    class _SpyRedis:
        """只记录「每次 execute 提交了哪些命令」，其余透传真 fakeredis（语义不打折）。"""

        def pipeline(self):
            return _SpyPipe(server.pipeline())

    monkeypatch.setattr(telemetry, "get_redis", lambda: _SpyRedis())

    assert telemetry._limited("1.2.3.4") is False
    assert executed == [("set", "incr")], (
        f"计数与 TTL 必须在同一次 pipeline.execute 内原子提交，实得 {executed}"
    )
    assert server.ttl("telemetry:rl:1.2.3.4") > 0


def test_memory_fallback_window_is_bounded(monkeypatch):
    """B7（审计 2026-09-27）：降级路径的内存窗口有界——匿名端点上每个见过的 IP 不得永久留档。

    defaultdict(deque) 版本里 1025 个 IP 会留下 1025 条记录；TTLCache 版本按 maxsize 封顶
    （并按 TTL 淘汰闲置 IP），手法同 P3-⑩ 修 _suggest_cache。
    """
    from app.api import telemetry

    def _redis_down(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(telemetry, "get_redis", _redis_down)
    telemetry._recent.clear()
    for i in range(telemetry._recent.maxsize + 10):
        telemetry._limited_memory(f"10.0.{i // 256}.{i % 256}")
    assert len(telemetry._recent) <= telemetry._recent.maxsize
    telemetry._recent.clear()


