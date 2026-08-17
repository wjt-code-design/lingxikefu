"""Audit Logs 路由（Phase 4）：/api/v1/admin/audit-logs 审计日志查询（仅 admin）。"""
from __future__ import annotations

from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.database import get_db
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogItem, AuditLogListResp

router = APIRouter(prefix="/admin", tags=["admin"])


def _item(a: AuditLog) -> AuditLogItem:
    return AuditLogItem(
        audit_id=str(a.id),
        actor_email=a.actor_email,
        actor_role=a.actor_role,
        action=a.action,
        resource=a.resource,
        resource_id=a.resource_id,
        detail=a.detail,
        ip=a.ip,
        created_at=a.created_at.isoformat(),
    )


@router.get("/audit-logs", response_model=AuditLogListResp)
def list_audit_logs(
    action: str | None = Query(default=None),
    resource: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AuditLogListResp:
    """审计日志列表：action/resource/actor(模糊 actor_email)/start/end 过滤，created_at desc。

    start/end 为 ISO 日期（YYYY-MM-DD），闭区间过滤（含首尾整天）。
    """
    cond = []
    if action:
        cond.append(AuditLog.action == action)
    if resource:
        cond.append(AuditLog.resource == resource)
    if actor:
        cond.append(AuditLog.actor_email.ilike(f"%{actor}%"))
    if start:
        cond.append(AuditLog.created_at >= datetime.combine(start, time.min))
    if end:
        cond.append(AuditLog.created_at <= datetime.combine(end, time.max))

    total = db.scalar(select(func.count(AuditLog.id)).where(*cond)) or 0
    rows = (
        db.scalars(
            select(AuditLog)
            .where(*cond)
            .order_by(AuditLog.created_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .all()
    )
    return AuditLogListResp(items=[_item(a) for a in rows], total=total)
