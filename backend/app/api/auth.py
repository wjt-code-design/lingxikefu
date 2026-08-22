"""Auth 路由（BU-02）：/api/v1/auth/register|login|refresh|logout|me。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.core.security import JWTError, decode_token
from app.core.token_revocation import revoke_token
from app.schemas.auth import (
    AuthResp,
    LoginReq,
    MeResp,
    RefreshReq,
    RefreshResp,
    RegisterReq,
)
from app.schemas.knowledge import OkResp
from app.services.auth import AuthError, AuthService
from app.services.quota import get_quota_service
from app.services.user_profile_service import reset_profile

router = APIRouter(prefix="/auth", tags=["auth"])

#: 登录 / 注册限流：每 IP 每分钟最多 5 次（防爆破 / 批量注册）
LOGIN_LIMIT = 5
LOGIN_WINDOW = 60


def _client_ip(request: Request) -> str:
    """限流维度 IP（第6组项3·XFF 信任边界）。

    仅当直连对端（TCP 对端，request.client.host）属于可信任反代白名单时，
    才采用 ``X-Forwarded-For`` 首段作为客户端 IP；否则用 TCP 对端本身。
    默认 ``TRUSTED_PROXIES`` 为空 → 忽略 XFF（fail-closed）：防客户端在直连场景
    伪造 X-Forwarded-For 绕过登录/注册 IP 限流。
    """
    peer = request.client.host if request.client else "unknown"
    if peer in settings.TRUSTED_PROXIES:
        fwd = request.headers.get("X-Forwarded-For")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


@router.post("/register", response_model=AuthResp, status_code=status.HTTP_201_CREATED)
def register(req: RegisterReq, request: Request, db: Session = Depends(get_db)) -> AuthResp:
    ip = _client_ip(request)
    if not rate_limit(f"ratelimit:register:{ip}", LOGIN_LIMIT, LOGIN_WINDOW):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "注册过于频繁，请稍后再试")
    svc = AuthService(db)
    try:
        user = svc.register(req)
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    access, refresh = svc.issue_tokens(user)
    return AuthResp(user_id=str(user.id), access_token=access, refresh_token=refresh)


@router.post("/login", response_model=AuthResp)
def login(req: LoginReq, request: Request, db: Session = Depends(get_db)) -> AuthResp:
    ip = _client_ip(request)
    if not rate_limit(f"ratelimit:login:{ip}", LOGIN_LIMIT, LOGIN_WINDOW):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "登录过于频繁，请稍后再试")
    svc = AuthService(db)
    try:
        user = svc.authenticate(req.account, req.password)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    access, refresh = svc.issue_tokens(user)
    return AuthResp(user_id=str(user.id), access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=RefreshResp)
def refresh(req: RefreshReq, db: Session = Depends(get_db)) -> RefreshResp:
    svc = AuthService(db)
    try:
        # R-4：轮换 —— access + 新 refresh（旧 refresh 已吊销）
        access, new_refresh = svc.refresh(req.refresh_token)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    return RefreshResp(access_token=access, refresh_token=new_refresh)


@router.post("/logout", response_model=OkResp)
def logout(
    req: RefreshReq,
    payload: dict = Depends(get_current_user),
) -> OkResp:
    """登出：吊销当前 access token 与提交的 refresh token（M1：旧 token 失效）。"""
    revoke_token(payload.get("jti"), payload.get("exp"))
    try:
        rp = decode_token(req.refresh_token)
        if rp.get("type") == "refresh":
            revoke_token(rp.get("jti"), rp.get("exp"))
    except JWTError:
        pass
    return OkResp()


@router.get("/me", response_model=MeResp)
def me(
    payload: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResp:
    svc = AuthService(db)
    user = svc.repo.get_by_id(UUID(payload["sub"]))
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    quota_left = get_quota_service().left_today(str(user.id))
    quota_total = get_quota_service().daily_limit()  # 方法是属性访问会传 bound method，导致 pydantic 校验失败
    return MeResp(
        user_id=str(user.id),
        email=user.email,
        phone=user.phone,
        role=user.role,
        quota_left=quota_left,
        quota_total=quota_total,
    )


@router.post("/me/profile/reset", response_model=OkResp)
def reset_my_profile(
    payload: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OkResp:
    """隐私自主控制（2026-08-22 Phase E）：清空当前用户的画像（长期记忆）。

    - 任何登录用户可清自己的画像（画像来源全为本人历史会话/反馈，隐私合规最小化）；
    - 清空后画像归零，后续不再注入（重新对话会重新采集）；
    - fail-open：无画像时返回 ok（幂等，重复调无副作用）。
    """
    user_id = UUID(payload["sub"])
    reset_profile(db, user_id)
    return OkResp()
