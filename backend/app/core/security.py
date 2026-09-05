"""JWT 安全工具（BU-02 Auth 模块完整实现）。

密码哈希（bcrypt 直调）/ JWT 签发与校验（PyJWT，替代 python-jose：维护停滞 + CVE）。
禁止在本文件硬编码任何密钥（密钥由 settings 注入）。

B2-5（2026-09-06 深度审查）：弃 passlib.CryptContext，直接 bcrypt.hashpw/checkpw。
根因：passlib 1.7.4 经 `bcrypt.__about__.__version__` 探测后端版本，该属性在
bcrypt 4.1+ 已移除 → 每次 hash/verify（即每次登录/注册）内部抛 AttributeError
再吞成 PasslibHashWarning；passlib 2020 后停维护。$2b$ 格式与存量哈希完全兼容，
零迁移。超长/非法哈希串统一 fail-safe 返回 False（不 500 认证链路）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt as pyjwt

from app.core.config import settings

ALGORITHM = "HS256"
#: 统一 JWT 异常基类（InvalidTokenError 涵盖 验签失败/格式坏/过期 等子类），
#: 供下游 auth/deps 捕获，与旧 python-jose 的 jose.JWTError 语义等价。
JWTError = pyjwt.InvalidTokenError


def hash_password(password: str) -> str:
    """bcrypt 哈希，禁止明文存储（红线：密码永不落库明文）。

    超长密码（UTF-8 >72 字节）由 RegisterReq 入口校验拒绝（bcrypt 本身对 >72
    字节会抛 ValueError），故此处不重复处理——注册路径已被 schema 挡住。
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """校验明文与存储哈希；非法/损坏哈希串或超长输入 → False（fail-safe，不抛）。

    bcrypt.checkpw 对格式非法的 hash、或 >72 字节的 password 会抛 ValueError，
    这里统一兜成 False：宁可让该次登录失败（用户可走密码重置），也不静默截断
    比对——截断会引入「前 72 字节相同即同密码」的可冒用等价类。
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


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
