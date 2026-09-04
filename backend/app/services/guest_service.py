"""匿名会话服务（D1 完整特性，2026-09-04 立项批次B）。

设计（规划裁定）：guest = 真实 User 行（role=user、status="guest"、email/phone 空、
password_hash 随机不可登录）+ 普通 access/refresh JWT（access 带 guest claim）。
Session/配额/通知/工单全链路按 user_id 复用，零架构分叉；匿名性由「无凭证不可登录、
管理面不可见、超期自动清理」三闸门保证。

- issue_guest：建 guest 行（IP 限发放由端点层 rate_limit 把关，本函数只管落库）；
- purge_expired_guests：删 created_at 超留存期的 guest 行——messages/sessions/
  feedback/user_profile 的 FK 全部 ondelete=CASCADE，删 user 即级联清全部痕迹；
  tickets.assignee_id 是 SET NULL 且 guest 永不被指派，不受影响。
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.security import hash_password
from app.models.user import User, UserRole

#: guest 行的 status 标记值（与 "active" 相对；登录路径不校验 status，
#: 但 guest 无 email/phone 且密码哈希随机，天然不可登录）
GUEST_STATUS = "guest"


def issue_guest(db: OrmSession) -> User:
    """创建并返回一个 guest 用户行（随机不可登录密码）。"""
    user = User(
        tenant_id=settings.TENANT_DEFAULT,
        email=None,
        phone=None,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        role=UserRole.user,
        status=GUEST_STATUS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def purge_expired_guests(db: OrmSession, days: int | None = None) -> int:
    """删除创建超期的 guest 行（FK CASCADE 级联清会话/消息/反馈/画像）。

    days<=0 → 关闭清理返回 0（配置语义对齐 AUTO_TICKET_* 阈值）。
    返回删除行数。
    """
    retention = days if days is not None else settings.GUEST_RETENTION_DAYS
    if retention <= 0:
        return 0
    # 方言兼容：SQLite 的 DateTime(timezone=True) 读回 naive，PG 读回 aware；
    # 查询边界统一去 tzinfo（PG timestamptz 按 UTC 比较，语义等价）。
    cutoff = (datetime.now(UTC) - timedelta(days=retention)).replace(tzinfo=None)
    ids = db.scalars(
        select(User.id).where(User.status == GUEST_STATUS, User.created_at < cutoff)
    ).all()
    if not ids:
        return 0
    db.execute(delete(User).where(User.id.in_(ids)))
    db.commit()
    return len(ids)
