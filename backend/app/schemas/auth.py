"""Auth 请求 / 响应模型（与 contracts/api.ts 字段逐一对应）。"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.models.user import UserRole


class LoginReq(BaseModel):
    account: str  # 邮箱或手机号
    password: str


class RegisterReq(BaseModel):
    email: str | None = Field(default=None, description="与 phone 至少填一个")
    phone: str | None = Field(default=None, description="与 email 至少填一个")
    password: str = Field(
        min_length=8,
        description="至少 8 位，且同时包含字母和数字（D1 密码强度）",
    )
    role: UserRole | None = None

    @field_validator("password")
    @classmethod
    def _check_password_complexity(cls, v: str) -> str:
        # 注意：pydantic-core 的 Rust regex 不支持 look-ahead，
        # 因此"同时包含字母和数字"的 AND 语义必须用 field_validator 实现（Python re）。
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("密码需同时包含字母和数字")
        return v


class AuthResp(BaseModel):
    user_id: str
    access_token: str
    refresh_token: str


class RefreshReq(BaseModel):
    refresh_token: str


class RefreshResp(BaseModel):
    access_token: str
    refresh_token: str  # R-4：轮换后返回新 refresh（旧 token 已吊销），前端需覆盖存储


class MeResp(BaseModel):
    user_id: str
    email: str | None = None
    phone: str | None = None
    role: UserRole
    quota_left: int
    quota_total: int  # 每日配额上限（契约曾声明但后端未返回 → 前端恒 undefined，2026-08-20 补齐）
