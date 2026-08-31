"""C1 红测（bughunt-concurrency Critical-1）：Redis 客户端 socket 超时配置。

redis_client.from_url 若不带 socket_timeout / socket_connect_timeout，
Redis 挂起（非宕机，如网络半开）时同步调用将永久阻塞——chat 热路径上
冻结整个事件循环，所有请求堆积。本测试锁死三个守护参数必须注入。
"""
from __future__ import annotations

import redis as redis_pkg
from app.core import redis_client as rc


def test_redis_client_configures_socket_timeouts(monkeypatch):
    captured: dict = {}

    def _fake_from_url(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return object()  # 占位客户端（不实际连接）

    monkeypatch.setattr(redis_pkg, "from_url", _fake_from_url)
    monkeypatch.setattr(rc, "_redis", None)  # 重置单例，强制重建

    rc.get_redis()

    assert captured.get("socket_timeout") == 2, "缺 socket_timeout：Redis 挂起将永久阻塞"
    assert captured.get("socket_connect_timeout") == 2, "缺 socket_connect_timeout：连接挂起将永久阻塞"
    assert captured.get("health_check_interval") == 30, "缺 health_check_interval：半开连接无法自愈"
