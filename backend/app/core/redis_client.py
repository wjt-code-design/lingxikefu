"""共享 Redis 客户端单例（M1 限流 / 吊销 / M2 配额统一复用）。"""
from __future__ import annotations

import threading

from app.core.config import settings

_redis = None
_lock = threading.Lock()


def get_redis():
    """进程内单例 Redis 客户端（decode_responses=True）；首次访问惰性创建。"""
    global _redis
    if _redis is None:
        with _lock:
            if _redis is None:
                import redis

                _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis
