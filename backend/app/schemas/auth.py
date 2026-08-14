"""Auth 请求 / 响应模型（与 contracts/api.ts 字段逐一对应）。"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.user import UserRole


class LoginReq(BaseModel):
    account: str  # 邮箱或手机号
    password: str


class RegisterReq(BaseModel):
    email: str | None = Field(default=None, description="与 phone 至少填一个")
    phone: str | None = Field(default=None, description="与 email 至少填一个")
    password: str = Field(min_length=6, description="至少 6 位")
    role: UserRole | None = None


class AuthResp(BaseModel):
    user_id: str
    access_token: str
    refresh_token: str


class RefreshReq(BaseModel):
    refresh_token: str


class RefreshResp(BaseModel):
    access_token: str


class MeResp(BaseModel):
    user_id: str
    email: str | None = None
    phone: str | None = None
    role: UserRole
    quota_left: int
