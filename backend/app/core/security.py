"""JWT 安全工具（BU-02 Auth 模块填充完整实现）。

本单元（BU-01）仅提供占位实现：函数签名已就位，依赖 python-jose[cryptography]，
真实签发 / 校验逻辑由 BU-02 在 auth 端点中接入。禁止在本文件硬编码任何密钥。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jose import jwt

from app.core.config import settings

ALGORITHM = "HS256"


def create_access_token(subject: str, role: str) -> str:
    """签发 access token（占位，BU-02 完善 claims / jti / 吊销校验）。"""
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def create_refresh_token(subject: str) -> str:
    """签发 refresh token（占位）。"""
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """解析并校验 token，失败抛 jose.JWTError（占位）。"""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
