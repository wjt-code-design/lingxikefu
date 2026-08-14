"""用户仓储（BU-02）。单租户：所有查询显式带 tenant_id。"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.user import User, UserRole


class UsersRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, user_id: UUID) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return (
            self.db.query(User)
            .filter_by(email=email, tenant_id=settings.TENANT_DEFAULT)
            .first()
        )

    def get_by_phone(self, phone: str) -> User | None:
        return (
            self.db.query(User)
            .filter_by(phone=phone, tenant_id=settings.TENANT_DEFAULT)
            .first()
        )

    def get_by_account(self, account: str) -> User | None:
        """account 可为邮箱或手机号。"""
        return self.get_by_email(account) or self.get_by_phone(account)

    def create(
        self,
        *,
        password_hash: str,
        email: str | None = None,
        phone: str | None = None,
        role: UserRole = UserRole.user,
    ) -> User:
        user = User(
            email=email,
            phone=phone,
            password_hash=password_hash,
            role=role,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
