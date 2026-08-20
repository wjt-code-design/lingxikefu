"""JWT 安全行为测试（第6组项1·PyJWT 迁移后安全等价）。

覆盖 decode_token 的失败路径（C-假绿：安全拒绝必须有测试）：
- 合法签发 → 字段完整可解（access 带 role，refresh 无 role）；
- 伪造/篡改/错 secret → 一律拒绝（InvalidTokenError，即 security.JWTError）；
- 过期 → 拒绝（ExpiredSignatureError 是 JWTError 子类，下游统一捕获）。
验证 PyJWT 与旧 python-jose 行为等价：全量 auth/token 测试已覆盖成功路径，此处补失败路径硬证据。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from app.core.security import ALGORITHM, JWTError, create_access_token, create_refresh_token, decode_token
from app.core.config import settings


def test_access_token_roundtrip_fields():
    tok = create_access_token("u-123", "admin")
    payload = decode_token(tok)
    assert payload["sub"] == "u-123"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"
    assert payload.get("jti")
    assert isinstance(payload.get("exp"), int)


def test_refresh_token_has_no_role():
    tok = create_refresh_token("u-123")
    payload = decode_token(tok)
    assert payload["type"] == "refresh"
    assert "role" not in payload


def test_decode_rejects_wrong_secret():
    # 用错误 secret 签 → 验签失败
    forged = pyjwt.encode({"sub": "x"}, "wrong-secret", algorithm=ALGORITHM)
    with pytest.raises(JWTError):
        decode_token(forged)


def test_decode_rejects_tampered_payload():
    """改 payload 保原签名（sub: user→admin）→ 验签必失败。"""
    import base64
    import json

    token = create_access_token("u-1", "user")
    header, _old_payload, sig = token.split(".")

    def b64(s: str) -> str:
        return base64.urlsafe_b64encode(s.encode()).rstrip(b"=").decode()

    tampered_claims = json.dumps(
        {"sub": "u-9", "role": "admin", "exp": int(datetime.now(UTC).timestamp()) + 3600},
        separators=(",", ":"),
    )
    tampered = f"{header}.{b64(tampered_claims)}.{sig}"
    with pytest.raises(JWTError):
        decode_token(tampered)


def test_decode_rejects_expired():
    expired = pyjwt.encode(
        {"sub": "u-1", "exp": int(datetime.now(UTC).timestamp()) - 100},
        settings.JWT_SECRET,
        algorithm=ALGORITHM,
    )
    with pytest.raises(JWTError):  # ExpiredSignatureError is subclass of InvalidTokenError
        decode_token(expired)