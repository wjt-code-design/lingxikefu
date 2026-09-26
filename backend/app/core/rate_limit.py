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
        # P3-⑪：计数键的创建与 TTL 落在同一 MULTI pipeline 里原子提交——消除「incr 成功、expire
        # 前崩溃 → 永不失效计数键」的永久误伤窗口。
        # B3（审计 2026-09-27）：TTL 只在窗口开启（计数 0→1）那次落地。此前每次请求都 expire，
        # 被拒请求同样把窗口重置回满值 ⇒ 客户端每 <window 秒重试一次就永不解锁（共享出口 IP 下
        # 一个人撞满配额会把同 IP 其他人长期锁死）。SET NX EX 先建带 TTL 的键、再 INCR：
        # 键已存在时 SET NX 是空操作（不碰 TTL），故窗口锚定在建键那一刻——这才是固定窗口。
        pipe = r.pipeline()
        pipe.set(key, 0, nx=True, ex=window)
        pipe.incr(key)
        results = pipe.execute()
        return int(results[1]) <= limit
    except Exception:  # noqa: BLE001 - Redis 不可用：降级放行（避免锁死登录）
        logger.warning("rate_limit: redis 不可用，放行请求")
        return True
