"""审计日志服务（Phase 4）：统一写入入口，异常不抛出（不阻塞主流程）。

- actor_email 未显式传入时按 actor_id 从 User 表补全（payload 只有 sub/role）；
- 任何异常仅 ``logging.warning`` + rollback，绝不抛出，保证埋点对原端点零影响（fail-open）。
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)


def _resolve_email(db: Session, actor_id: str) -> str | None:
    """按 actor_id 从 User 表查 email；actor_id 非 UUID 或查无此人不报错（返回 None）。"""
    try:
        uid = uuid.UUID(str(actor_id))
    except (ValueError, TypeError):
        return None
    u = db.get(User, uid)
    return u.email if u else None


def audit_log(
    db: Session,
    actor_id: str,
    actor_email: str | None = None,
    actor_role: str | None = None,
    action: str = "",
    resource: str = "",
    resource_id: str | None = None,
    detail: str | None = None,
    ip: str | None = None,
) -> None:
    """写入一条审计日志；任何异常仅告警 + 回滚，绝不抛出（埋点零侵入）。"""
    try:
        if actor_email is None:
            actor_email = _resolve_email(db, actor_id)
        db.add(
            AuditLog(
                actor_id=actor_id,
                actor_email=actor_email,
                actor_role=actor_role,
                action=action,
                resource=resource,
                resource_id=resource_id,
                detail=detail,
                ip=ip,
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001 - 审计失败不阻断主流程
        db.rollback()
        logger.warning(
            "审计日志写入失败（已忽略，不阻塞主流程）: action=%s resource=%s resource_id=%s",
            action,
            resource,
            resource_id,
        )
