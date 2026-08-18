"""Token 吊销测试（M1 + S1）：Redis 故障时 fail-closed，防已吊销 token 复活 / refresh 并发重放。"""
from __future__ import annotations

from unittest.mock import patch

from app.core import token_revocation

_FUTURE_EXP = 9999999999  # 远未来时间戳，保证 TTL 为正


def _redis_down():
    raise RuntimeError("redis connection refused")


class _SeqRedis:
    """按调用序号返回结果的假 Redis（模拟 SETNX 语义：首次 True，重复 False）。"""

    def __init__(self):
        self.n = 0

    def set(self, key, value, ex=None, nx=False):
        self.n += 1
        return self.n == 1

    def exists(self, key):
        return True


def test_consume_token_fail_closed_when_redis_down():
    """S1：Redis 故障时 refresh 换发必须拒绝（fail-closed），防止并发重放无限续期。"""
    with patch("app.core.token_revocation.get_redis", side_effect=_redis_down):
        assert token_revocation.consume_token("jti-1", _FUTURE_EXP) is False


def test_is_revoked_fail_closed_when_redis_down():
    """S1：Redis 故障时 access 校验视为已吊销（fail-closed），防止已登出 token 存活。"""
    with patch("app.core.token_revocation.get_redis", side_effect=_redis_down):
        assert token_revocation.is_revoked("jti-1") is True


def test_revoke_token_best_effort_when_redis_down():
    """S1：登出写黑名单 best-effort —— Redis 故障不抛异常（不阻断登出接口）。"""
    with patch("app.core.token_revocation.get_redis", side_effect=_redis_down):
        token_revocation.revoke_token("jti-1", _FUTURE_EXP)  # 不应抛异常


def test_consume_token_success_and_replay():
    """正常路径：SETNX 首次成功返回 True，同一 jti 再次使用返回 False（防并发重放）。"""
    with patch("app.core.token_revocation.get_redis", return_value=_SeqRedis()):
        assert token_revocation.consume_token("jti-x", _FUTURE_EXP) is True
        assert token_revocation.consume_token("jti-x", _FUTURE_EXP) is False


def test_is_revoked_true_when_blacklisted():
    """正常路径：黑名单中存在该 jti → 视为已吊销。"""
    with patch("app.core.token_revocation.get_redis", return_value=_SeqRedis()):
        assert token_revocation.is_revoked("jti-black") is True


def test_empty_jti_bypass():
    """无 jti 的旧格式 token：无法防重放，放行（consume True / is_revoked False）。"""
    with patch("app.core.token_revocation.get_redis", side_effect=_redis_down):
        assert token_revocation.consume_token(None, _FUTURE_EXP) is True
        assert token_revocation.is_revoked(None) is False
