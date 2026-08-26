"""配额服务（BU-08）：每用户每日问答次数，Redis 计数器。

设计要点：
- 计数键按日期隔离，TTL 48h 自动清理。
- ``try_consume``：原子闸门 —— INCR 后比对上限，超额回滚并拒绝（修复 M2 TOCTOU 竞态）。
- R2 幂等/回滚：``try_consume`` 支持 ``idem_key``（客户端提问幂等键 client_msg_id），
  同一请求重试（断连/超时后重发）命中幂等标记 → 不重复扣费；
  调用方在失败路径（断连/知识库为空/系统异常）调 ``refund`` 回滚已扣配额，解决断连白扣。
- Redis 不可用时 ``try_consume`` **fail-closed 拒绝**（而非放行），保证配额保护不失效；
  ``left_today``/``used_today`` 仅供 /quota 展示，Redis 不可达时优雅返回 0/满额（不 5xx）；
  ``refund`` fail-open（回滚失败不阻塞主流程，重试重新扣费兜底）。
- redis 客户端可注入，便于单测用内存假对象替换（无需真起 Redis）。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date

from app.core.config import settings
from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

#: R2：幂等标记 TTL（与配额计数 key 一致，48h 自动清理）
_IDEM_TTL_SECONDS = 60 * 60 * 48


def _today() -> str:
    return date.today().isoformat()


class QuotaService:
    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    def daily_limit(self) -> int:
        return settings.DAILY_QUOTA_LIMIT

    def _key(self, user_id: str, day: str | None = None) -> str:
        return f"quota:{user_id}:{day or _today()}"

    def _idem_key(self, idem_key: str) -> str:
        return f"quota:idem:{idem_key}"

    def used_today(self, user_id: str, day: str | None = None) -> int:
        try:
            return int(self.redis.get(self._key(user_id, day)) or 0)
        except Exception:  # Redis 不可达 → 视为 0 已用（仅展示用）
            return 0

    def left_today(self, user_id: str) -> int:
        return max(0, self.daily_limit() - self.used_today(user_id))

    def try_consume(self, user_id: str, n: int = 1, idem_key: str | None = None, content: str | None = None) -> tuple[bool, int]:
        """原子扣减闸门（P1-①）：INCR + expire + set(marker) 并入 MULTI pipeline 原子执行。

        R2 幂等指纹：marker 绑定 ``sha256(user_id:content)``（``content=None`` 退化为
        ``sha256(user_id:idem_key)`` 兼容旧调用）——跨用户复用 idem_key / 同用户换
        content 均不再共享幂等标记，杜绝裸 marker 免费放行。

        返回 (allowed, used)：见模块 docstring。
        Redis / pipeline 不可用时 fail-closed 返回 (False, 0)（不产生半步脏状态）。
        """
        try:
            r = self.redis
            marker = None
            if idem_key:
                material = content if content else idem_key
                fingerprint = hashlib.sha256(f"{user_id}:{material}".encode()).hexdigest()
                marker = self._idem_key(fingerprint)
                if r.get(marker) is not None:
                    # 幂等命中：同用户同内容此前已扣过费（重试），本次不重复扣
                    return True, self.used_today(user_id)
            key = self._key(user_id)
            pipe = r.pipeline()
            pipe.incr(key, n)
            pipe.expire(key, 60 * 60 * 48)
            used = int(pipe.execute()[0])
            if used > self.daily_limit():
                r.decr(key, n)  # 超额回滚，避免占用配额
                return False, used - n
            if marker:
                r.set(marker, "1", ex=_IDEM_TTL_SECONDS)
            return True, used
        except Exception:  # noqa: BLE001 - Redis 不可用：fail-closed 拒绝而非放行
            logger.warning("quota try_consume: redis 不可用，fail-closed 拒绝")
            return False, 0

    def refund(self, user_id: str, n: int = 1, idem_key: str | None = None, content: str | None = None) -> None:
        """失败回滚（P1-①）：按与 try_consume 相同的指纹定位标记并清除，回滚已扣配额。

        回滚入参（含 content）与扣费入参一致才命中同一枚标记；回滚后删除标记，
        重试（同一请求）将重新正常扣费。marker 不存在 = 未扣费，无动作。
        Redis 不可达时 fail-open（不阻塞主流程）。
        """
        try:
            r = self.redis
            if idem_key:
                material = content if content else idem_key
                fingerprint = hashlib.sha256(f"{user_id}:{material}".encode()).hexdigest()
                marker = self._idem_key(fingerprint)
                if r.get(marker) is None:
                    return  # 未扣费（或已回滚）→ 无动作
                r.decr(self._key(user_id), n)
                r.delete(marker)
            else:
                r.decr(self._key(user_id), n)
        except Exception:  # noqa: BLE001 - fail-open：回滚失败不阻塞
            logger.warning("quota refund: redis 不可用，跳过回滚", exc_info=True)


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
