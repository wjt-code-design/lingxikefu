"""FastAPI 共享依赖：Bearer 鉴权。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import JWTError, decode_token
from app.core.token_revocation import is_revoked

_bearer = HTTPBearer()


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """解析并校验 Bearer token，返回 payload（含 sub / role）。"""
    try:
        payload = decode_token(creds.credentials)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # H1（外部审查 2026-08-22）：令牌类型校验——refresh 与 access 同密钥签发且同样带
    # sub/jti，不校验 type 则 7 天有效期的 refresh token 可直接通过全部用户端守卫，
    # 绕过轮换机制的吊销窗口（/auth/refresh 侧已校验 type=="refresh"，此处补对称防线）
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # M1：登出后吊销的 token 立即失效
    if is_revoked(payload.get("jti")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def require_admin(payload: dict = Depends(get_current_user)) -> dict:
    """仅 admin 角色可访问（管理后台 / 知识库写操作）。"""
    if payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return payload
