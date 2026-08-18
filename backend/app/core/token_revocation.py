"""Token 吊销（M1）：基于 jti 的 Redis 黑名单，支持登出后旧 token 失效。

- refresh token 与 access token 均带 jti；logout 将两者 jti 写入黑名单，TTL=剩余有效期。
- Redis 不可用时 **fail-closed（S1）**：宁可拒绝放行，也不允许「已吊销 token 复活 / refresh 并发重放」。
  - consume_token：Redis 故障 → 返回 False（拒绝 refresh 换发，用户重新登录即可恢复）；
  - is_revoked：Redis 故障 → 视为已吊销（拒绝 access，防已登出 token 存活）；
  - revoke_token：best-effort 写黑名单（失败不抛，不阻断登出；窗口期安全由 is_revoked fail-closed 兜底）。
"""
from __future__ import annotations

import logging
import time

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

_PREFIX = "revoked:"


def revoke_token(jti: str | None, exp: int | None) -> None:
    if not jti:
        return
    try:
        r = get_redis()
        ttl = int((exp or 0) - time.time())
        r.set(_PREFIX + jti, "1", ex=max(ttl, 1))
    except Exception:  # noqa: BLE001 - Redis 不可用：best-effort，跳过写入（登出接口不阻断）
        logger.warning("revoke_token: redis 不可用，跳过黑名单写入")


def consume_token(jti: str | None, exp: int | None) -> bool:
    """原子占用一次 token（R-4 竞态修复）：SETNX 成功=首次使用可继续；失败=已被占用/吊销，拒绝。

    替代原 refresh 轮换的 is_revoked+revoke 两步（check-then-act 竞态：并发复用同一
    refresh token 可双双通过检查并换发新 token）。SETNX 原子保证同一 jti 仅一次成功；
    logout 吊销写入同一 key，语义统一为"key 存在即拒绝"。
    **S1 fail-closed：Redis 不可用时返回 False（拒绝换发），防止并发重放无限续期——宁可让用户重新登录。**
    """
    if not jti:
        return True  # 无 jti 无法防重放（旧格式 token），降级放行
    try:
        r = get_redis()
        ttl = int((exp or 0) - time.time())
        return bool(r.set(_PREFIX + jti, "1", ex=max(ttl, 1), nx=True))
    except Exception:  # noqa: BLE001 - Redis 不可用：fail-closed，拒绝换发
        logger.warning("consume_token: redis 不可用，拒绝 refresh 换发（fail-closed）")
        return False


def is_revoked(jti: str | None) -> bool:
    if not jti:
        return False
    try:
        r = get_redis()
        return bool(r.exists(_PREFIX + jti))
    except Exception:  # noqa: BLE001 - Redis 不可用：fail-closed，视为已吊销
        logger.warning("is_revoked: redis 不可用，视为已吊销（fail-closed）")
        return True
