"""XFF 信任边界测试（第6组项3）：限流维度 IP 只在可信反代后信任 X-Forwarded-For。

失败路径（pitfall B-信任边界）：直连场景伪造 XFF 不得绕过——默认 TRUSTED_PROXIES 为空
→ 恒用 TCP 对端（fail-closed）；仅当对端 IP 在可信白名单时才取 XFF 首段。
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.api.auth import _client_ip
from app.core.config import settings


def _req(client_host: str = "9.9.9.9", xff: str | None = None) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/auth/login",
        "headers": [],
        "client": (client_host, 12345),
        "server": ("testserver", 80),
    }
    if xff is not None:
        scope["headers"] = [(b"x-forwarded-for", xff.encode())]
    return Request(scope)


def test_no_trusted_proxies_ignores_xff(monkeypatch):
    """默认（白名单空）：即便伪造 XFF，也用 TCP 对端（fail-closed，防绕过）。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", [])
    req = _req(client_host="9.9.9.9", xff="1.2.3.4")
    assert _client_ip(req) == "9.9.9.9"  # 伪造 XFF 被忽略


def test_trusted_proxy_uses_xff_first(monkeypatch):
    """对端在可信白名单 → 取 XFF 首段。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["10.0.0.5"])
    req = _req(client_host="10.0.0.5", xff="203.0.113.7, 10.0.0.5")
    assert _client_ip(req) == "203.0.113.7"  # 首段 = 客户端 IP


def test_peer_not_trusted_ignores_xff(monkeypatch):
    """对端不在白名单（攻击者直连假扮）→ 即使带 XFF 也不用。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["10.0.0.5"])
    req = _req(client_host="9.9.9.9", xff="1.2.3.4")
    assert _client_ip(req) == "9.9.9.9"


def test_trusted_proxy_no_xff_falls_back_peer(monkeypatch):
    """可信反代未带 XFF → 回退对端本身（不崩溃）。"""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", ["10.0.0.5"])
    req = _req(client_host="10.0.0.5")  # 无 XFF
    assert _client_ip(req) == "10.0.0.5"


def test_no_client_falls_back_unknown():
    """极边界：无 client → 'unknown'。"""
    scope = {"type": "http", "headers": []}
    assert _client_ip(Request(scope)) == "unknown"