"""Auth 业务逻辑（BU-02）。"""
from __future__ import annotations

from uuid import UUID

from app.core.security import JWTError
from sqlalchemy.exc import IntegrityError

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.token_revocation import consume_token, revoke_token
from app.models.user import User, UserRole
from app.repositories.users import UsersRepository
from app.schemas.auth import RegisterReq


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
        try:
            return self.repo.create(
                email=req.email,
                phone=req.phone,
                password_hash=hash_password(req.password),
                role=role,
            )
        except IntegrityError:  # Bug 修复：并发注册同邮箱/手机号 → DB 唯一约束兜底 → 转可操作错误而非 500
            self.repo.db.rollback()
            raise AuthError("该邮箱或手机号已注册") from None

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

    def refresh(self, token: str) -> tuple[str, str]:
        """轮换 refresh token（R-4）：原子占用旧 jti + 签发新 access 与新 refresh。

        每次 refresh 换发新 token（7 天滚动窗口），旧 refresh 立即失效（原子占用防并发复用），
        缩小泄漏窗口；返回 (access_token, new_refresh_token)。
        """
        try:
            payload = decode_token(token)
        except JWTError as e:
            raise AuthError("refresh token 无效") from e
        if payload.get("type") != "refresh":
            raise AuthError("不是 refresh token")
        # 原子占用（SETNX）：并发复用同一 refresh token 时仅首个成功，防双签发竞态
        if not consume_token(payload.get("jti"), payload.get("exp")):
            raise AuthError("refresh token 已失效，请重新登录")
        user = self.repo.get_by_id(UUID(payload["sub"]))
        if not user:
            raise AuthError("用户不存在")
        return (
            create_access_token(str(user.id), user.role.value),
            create_refresh_token(str(user.id)),
        )
