"""BU-01 首版迁移：全表（含 tenant_id） + tenant_id 索引（配额改用 Redis 计数，无 quotas 表）

Revision ID: 0001
Revises:
Create Date: 2026-08-15

- 所有表第一个非主键列即 tenant_id（MVP 固定单值 'default'，Phase3 启用行级过滤）。
- 与 app/models/* 一一对应；任何模型变更必须在此双写 upgrade()/downgrade()。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- users ----------
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.Enum("admin", "agent", "user", name="user_role"), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("phone", name="uq_users_phone"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    # ---------- sessions ----------
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sessions_tenant_id", "sessions", ["tenant_id"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # ---------- messages ----------
    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Enum("user", "assistant", name="message_role"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("intent", sa.String(length=64), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])
    op.create_index("ix_messages_session_id", "messages", ["session_id"])

    # ---------- message_sources（知识来源唯一真源） ----------
    op.create_table(
        "message_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("doc_id", sa.Uuid(), nullable=False),
        sa.Column("doc_title", sa.String(length=255), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_message_sources_tenant_id", "message_sources", ["tenant_id"])
    op.create_index("ix_message_sources_message_id", "message_sources", ["message_id"])
    op.create_index("ix_message_sources_chunk_id", "message_sources", ["chunk_id"])
    op.create_index("ix_message_sources_doc_id", "message_sources", ["doc_id"])

    # ---------- knowledge_bases ----------
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_bases_tenant_id", "knowledge_bases", ["tenant_id"])
    op.create_index("ix_knowledge_bases_name", "knowledge_bases", ["name"])

    # ---------- documents ----------
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("kb_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("parsing", "embedding", "indexed", "failed", name="document_status"),
            nullable=False,
            server_default="parsing",
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_documents_tenant_id", "documents", ["tenant_id"])
    op.create_index("ix_documents_kb_id", "documents", ["kb_id"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])

    # ---------- chunks ----------
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("doc_id", sa.Uuid(), nullable=False),
        sa.Column("kb_id", sa.Uuid(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["doc_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("doc_id", "idx", name="uq_chunks_doc_idx"),
    )
    op.create_index("ix_chunks_tenant_id", "chunks", ["tenant_id"])
    op.create_index("ix_chunks_doc_id", "chunks", ["doc_id"])
    op.create_index("ix_chunks_kb_id", "chunks", ["kb_id"])

    # ---------- chunk_context（Phase2 预留：相邻块扩展） ----------
    op.create_table(
        "chunk_context",
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("prev_id", sa.Uuid(), nullable=True),
        sa.Column("next_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("chunk_id"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prev_id"], ["chunks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["next_id"], ["chunks.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_chunk_context_tenant_id", "chunk_context", ["tenant_id"])

    # ---------- feedback ----------
    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Enum("up", "down", name="feedback_rating"), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_feedback_tenant_id", "feedback", ["tenant_id"])
    op.create_index("ix_feedback_message_id", "feedback", ["message_id"])
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])

    # ---------- tickets（Phase2 预留） ----------
    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("open", "processing", "resolved", "closed", name="ticket_status"),
            nullable=False,
            server_default="open",
        ),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("external_ref", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_tickets_tenant_id", "tickets", ["tenant_id"])
    op.create_index("ix_tickets_session_id", "tickets", ["session_id"])


def downgrade() -> None:
    # 先删引用方（子表），再删被引用方（父表）
    for table in (
        "tickets",
        "feedback",
        "chunk_context",
        "chunks",
        "documents",
        "message_sources",
        "messages",
        "sessions",
        "knowledge_bases",
        "users",
    ):
        op.drop_table(table)

    # 显式回收 PostgreSQL enum 类型（drop_table 不级联删除 enum type）
    bind = op.get_bind()
    for enum_name in ("user_role", "message_role", "document_status", "feedback_rating", "ticket_status"):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
