"""配额服务（BU-08）：每用户每日问答次数，Redis 计数器。

设计要点：
- Redis 不可用时**优雅降级**（返回「已用 0、剩余=上限」），不阻断 /me、/quota、问答主流程；
- 计数键按日期隔离，TTL 48h 自动清理；
- redis 客户端可注入，便于单测用内存假对象替换（无需真起 Redis）。
"""
from __future__ import annotations

from datetime import date

from app.core.config import settings


def _today() -> str:
    return date.today().isoformat()


class QuotaService:
    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client

    @property
    def redis(self):
        if self._redis is None:
            import redis

            self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    def daily_limit(self) -> int:
        return settings.DAILY_QUOTA_LIMIT

    def _key(self, user_id: str, day: str | None = None) -> str:
        return f"quota:{user_id}:{day or _today()}"

    def used_today(self, user_id: str, day: str | None = None) -> int:
        try:
            return int(self.redis.get(self._key(user_id, day)) or 0)
        except Exception:  # Redis 不可达 → 降级视为 0 已用
            return 0

    def left_today(self, user_id: str) -> int:
        return max(0, self.daily_limit() - self.used_today(user_id))

    def increment(self, user_id: str, n: int = 1) -> int:
        """消费配额，返回最新已用数。Redis 不可达时返回 0（不阻断问答）。"""
        try:
            key = self._key(user_id)
            used = self.redis.incr(key, n)
            self.redis.expire(key, 60 * 60 * 48)
            return int(used)
        except Exception:
            return 0


_quota_service: QuotaService | None = None


def get_quota_service(redis_client=None) -> QuotaService:
    """业务路径使用模块级单例（复用 Redis 连接，避免每次请求重建实例）；

    测试可显式传入 redis_client 获取隔离的新实例。
    """
    global _quota_service
    if redis_client is not None:
        return QuotaService(redis_client=redis_client)
    if _quota_service is None:
        _quota_service = QuotaService()
    return _quota_service
