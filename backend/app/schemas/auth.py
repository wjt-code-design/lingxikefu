"""Auth 请求 / 响应模型（与 contracts/api.ts 字段逐一对应）。"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from app.models.user import UserRole


class LoginReq(BaseModel):
    account: str  # 邮箱或手机号
    password: str  # 不加 max_length：超长由 verify_password fail-safe 拒（避免 422/401 语义混乱）


class RegisterReq(BaseModel):
    email: str | None = Field(default=None, description="与 phone 至少填一个")
    phone: str | None = Field(default=None, description="与 email 至少填一个")
    password: str = Field(
        min_length=8,
        description="至少 8 位，且同时包含字母和数字（D1 密码强度）",
    )
    # P4：无 role 字段——注册恒为 user，不向调用方暴露可"提权"的假入口
    # （旧版曾声明 role 再拒绝；如今直接不声明，注入即被 pydantic 忽略）

    @field_validator("password")
    @classmethod
    def _check_password_complexity(cls, v: str) -> str:
        # 注意：pydantic-core 的 Rust regex 不支持 look-ahead，
        # 因此"同时包含字母和数字"的 AND 语义必须用 field_validator 实现（Python re）。
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("密码需同时包含字母和数字")
        # B2-5：bcrypt 对 >72 字节的口令静默截断 → 前 72 字节相同即同密码（可冒用等价类）。
        # 注册侧按 UTF-8 字节数拒绝（非字符数——多字节口令字符数 <72 仍可能超字节）。
        if len(v.encode("utf-8")) > 72:
            raise ValueError("密码过长（UTF-8 不超过 72 字节）")
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
