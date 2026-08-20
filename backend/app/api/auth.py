"""Auth 路由（BU-02）：/api/v1/auth/register|login|refresh|logout|me。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.rate_limit import rate_limit
from app.core.security import decode_token
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

router = APIRouter(prefix="/auth", tags=["auth"])

#: 登录 / 注册限流：每 IP 每分钟最多 5 次（防爆破 / 批量注册）
LOGIN_LIMIT = 5
LOGIN_WINDOW = 60


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


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
