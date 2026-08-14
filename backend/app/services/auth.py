"""Auth 业务逻辑（BU-02）。"""
from __future__ import annotations

from uuid import UUID

from jose import JWTError

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.repositories.users import UsersRepository
from app.schemas.auth import RegisterReq
from app.models.user import User, UserRole


class AuthError(Exception):
    """认证相关可预期错误（调用方转 HTTP 状态码）。"""


class AuthService:
    def __init__(self, db) -> None:
        self.repo = UsersRepository(db)

    def register(self, req: RegisterReq) -> User:
        if not req.email and not req.phone:
            raise AuthError("email 或 phone 至少提供一个")
        if req.email and self.repo.get_by_email(req.email):
            raise AuthError("该邮箱已注册")
        if req.phone and self.repo.get_by_phone(req.phone):
            raise AuthError("该手机号已注册")
        role = req.role or UserRole.user
        # 安全红线：匿名注册只允许普通用户，禁止提权为 admin/agent
        if role != UserRole.user:
            raise AuthError("注册仅允许 user 角色")
        return self.repo.create(
            email=req.email,
            phone=req.phone,
            password_hash=hash_password(req.password),
            role=role,
        )

    def authenticate(self, account: str, password: str) -> User:
        user = self.repo.get_by_account(account)
        if not user or not verify_password(password, user.password_hash):
            raise AuthError("账号或密码错误")
        return user

    @staticmethod
    def issue_tokens(user: User) -> tuple[str, str]:
        return (
            create_access_token(str(user.id), user.role.value),
            create_refresh_token(str(user.id)),
        )

    def refresh(self, token: str) -> str:
        try:
            payload = decode_token(token)
        except JWTError as e:
            raise AuthError("refresh token 无效") from e
        if payload.get("type") != "refresh":
            raise AuthError("不是 refresh token")
        user = self.repo.get_by_id(UUID(payload["sub"]))
        if not user:
            raise AuthError("用户不存在")
        return create_access_token(str(user.id), user.role.value)
