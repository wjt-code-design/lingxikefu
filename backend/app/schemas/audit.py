"""审计日志响应模型（Phase 4），与 /admin/audit-logs 对齐。"""
from __future__ import annotations

from pydantic import BaseModel


class AuditLogItem(BaseModel):
    audit_id: str
    actor_email: str | None = None
    actor_role: str | None = None
    action: str
    resource: str
    resource_id: str | None = None
    detail: str | None = None
    ip: str | None = None
    created_at: str


class AuditLogListResp(BaseModel):
    items: list[AuditLogItem]
    total: int
