"""ORM 模型统一出口：import 本包即完成所有表注册到 Base.metadata。"""
from app.models.app_setting import AppSetting
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.feedback import Feedback, FeedbackRating
from app.models.kb_publish import KBBatchStatus, KBPublishBatch
from app.models.knowledge import (
    Chunk,
    ChunkContext,
    Document,
    DocumentStatus,
    KnowledgeBase,
)
from app.models.message import Message, MessageRole, MessageSource
from app.models.notification import Notification
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User, UserRole
from app.models.user_profile import UserProfile

__all__ = [
    "Base",
    "AppSetting",
    "AuditLog",
    "Notification",
    "User",
    "UserRole",
    "Session",
    "Message",
    "MessageRole",
    "MessageSource",
    "KnowledgeBase",
    "KBBatchStatus",
    "KBPublishBatch",
    "Document",
    "DocumentStatus",
    "Chunk",
    "ChunkContext",
    "Feedback",
    "FeedbackRating",
    "Ticket",
    "TicketStatus",
    "UserProfile",
]
