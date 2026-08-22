"""JWT 安全工具（BU-02 Auth 模块完整实现）。

密码哈希（pbkdf2）/ JWT 签发与校验（PyJWT，替代 python-jose：维护停滞 + CVE）。
禁止在本文件硬编码任何密钥（密钥由 settings 注入）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from passlib.context import CryptContext

from app.core.config import settings

ALGORITHM = "HS256"
#: 统一 JWT 异常基类（InvalidTokenError 涵盖 验签失败/格式坏/过期 等子类），
#: 供下游 auth/deps 捕获，与旧 python-jose 的 jose.JWTError 语义等价。
JWTError = pyjwt.InvalidTokenError
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """bcrypt 哈希，禁止明文存储（红线：密码永不落库明文）。"""
    return _pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """校验明文与存储哈希。"""
    return _pwd_context.verify(password, hashed)


def create_access_token(subject: str, role: str) -> str:
    """签发 access token（M1：带 jti + type 以支持吊销）。"""
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return pyjwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def create_refresh_token(subject: str) -> str:
    """签发 refresh token（M1：带 jti 以支持吊销 / 登出失效）。"""
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return pyjwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """解析并校验 token（验签 + exp），失败抛 JWTError（PyJWT InvalidTokenError）。"""
    return pyjwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
