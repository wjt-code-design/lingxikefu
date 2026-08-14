"""Auth 路由（BU-02）：/api/v1/auth/register|login|refresh|me。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.schemas.auth import (
    AuthResp,
    LoginReq,
    MeResp,
    RefreshReq,
    RefreshResp,
    RegisterReq,
)
from app.services.auth import AuthError, AuthService
from app.services.quota import QuotaService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResp, status_code=status.HTTP_201_CREATED)
def register(req: RegisterReq, db: Session = Depends(get_db)) -> AuthResp:
    svc = AuthService(db)
    try:
        user = svc.register(req)
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    access, refresh = svc.issue_tokens(user)
    return AuthResp(user_id=str(user.id), access_token=access, refresh_token=refresh)


@router.post("/login", response_model=AuthResp)
def login(req: LoginReq, db: Session = Depends(get_db)) -> AuthResp:
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
        access = svc.refresh(req.refresh_token)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    return RefreshResp(access_token=access)


@router.get("/me", response_model=MeResp)
def me(
    payload: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResp:
    svc = AuthService(db)
    user = svc.repo.get_by_id(UUID(payload["sub"]))
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    quota_left = QuotaService().left_today(str(user.id))
    return MeResp(
        user_id=str(user.id),
        email=user.email,
        phone=user.phone,
        role=user.role,
        quota_left=quota_left,
    )
