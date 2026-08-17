"""通知中心响应模型（SSE），与 /notifications 端点对齐。"""
from __future__ import annotations

from pydantic import BaseModel


class NotificationItem(BaseModel):
    notification_id: str
    event_type: str
    title: str
    content: str = ""
    resource_type: str | None = None
    resource_id: str | None = None
    is_read: bool = False
    created_at: str = ""


class NotificationListResp(BaseModel):
    items: list[NotificationItem]
    total: int


class UnreadCountResp(BaseModel):
    count: int
