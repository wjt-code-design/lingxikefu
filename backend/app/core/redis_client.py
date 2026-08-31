"""共享 Redis 客户端单例（M1 限流 / 吊销 / M2 配额统一复用）。"""
from __future__ import annotations

import threading

from app.core.config import settings

_redis = None
_lock = threading.Lock()


def get_redis():
    """进程内单例 Redis 客户端（decode_responses=True）；首次访问惰性创建。

    C1（bughunt-concurrency）：必须带 socket 超时——Redis 挂起（非宕机，网络半开）
    时无超时的同步调用会永久阻塞，chat 热路径上直接冻结事件循环。
    health_check_interval 让连接池借出前 PING，半开连接可自愈。
    """
    global _redis
    if _redis is None:
        with _lock:
            if _redis is None:
                import redis

                _redis = redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    socket_timeout=2,
                    socket_connect_timeout=2,
                    health_check_interval=30,
                )
    return _redis
