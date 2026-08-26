"""固定窗口计数限流（M1）：登录 / 注册防爆破。Redis 不可用则放行。"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)


def rate_limit(key: str, limit: int, window: int) -> bool:
    """返回 True=允许，False=超限。key 在 window 秒内计数，超过 limit 即拒绝。

    RATE_LIMIT_ENABLED=false 时直接放行（测试/内部环境，避免用例间相互击穿）。
    """
    if not settings.RATE_LIMIT_ENABLED:
        return True
    try:
        r = get_redis()
        # P3-⑪：incr+expire 并入同一 MULTI pipeline 原子提交——消除「incr 成功、expire 前崩溃
        # → 永不失效计数键」导致的永久限流误伤（固定窗口语义不变：每次计数顺带刷新 TTL）。
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        results = pipe.execute()
        return int(results[0]) <= limit
    except Exception:  # noqa: BLE001 - Redis 不可用：降级放行（避免锁死登录）
        logger.warning("rate_limit: redis 不可用，放行请求")
        return True
