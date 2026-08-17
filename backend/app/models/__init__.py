"""ORM 模型统一出口：import 本包即完成所有表注册到 Base.metadata。"""
from app.models.base import Base
from app.models.audit import AuditLog
from app.models.feedback import Feedback, FeedbackRating
from app.models.knowledge import (
    Chunk,
    ChunkContext,
    Document,
    DocumentStatus,
    KnowledgeBase,
)
from app.models.message import Message, MessageRole, MessageSource
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User, UserRole

__all__ = [
    "Base",
    "AuditLog",
    "User",
    "UserRole",
    "Session",
    "Message",
    "MessageRole",
    "MessageSource",
    "KnowledgeBase",
    "Document",
    "DocumentStatus",
    "Chunk",
    "ChunkContext",
    "Feedback",
    "FeedbackRating",
    "Ticket",
    "TicketStatus",
]
