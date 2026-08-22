"""知识库 / 文档 / 切片模型（BU-04 填充业务逻辑）。

- chunks 在 Qdrant 有 payload 镜像（chunk_id/doc_id/tenant_id/source）。
- chunk_context 为 Phase2 预留：相邻块扩展（chunk_id 作主键，无独立 id）。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, tenant_id_column


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class DocumentStatus(StrEnum):
    parsing = "parsing"
    embedding = "embedding"
    indexed = "indexed"
    failed = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    kb_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        sa.Enum(DocumentStatus, name="document_status"),
        nullable=False,
        default=DocumentStatus.parsing,
        server_default=DocumentStatus.parsing.value,
    )
    sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    # 解析后的纯文本（上传时提取入库，worker 只做切片/向量化；扫描件 PDF 为 None → failed）
    raw_text: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    chunk_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        sa.UniqueConstraint("doc_id", "idx", name="uq_chunks_doc_idx"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = tenant_id_column()
    doc_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idx: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)


class ChunkContext(Base):
    """Phase2 预留：相邻块扩展（chunk_id 主键，无独立 id，第一个非主键列即 tenant_id）。

    ⚠️ 当前全仓无任何读写路径（2026-08-22 外部审查核实）——表结构与模型均为
    BU-01 规划书的有意预留，勿据此推断在用；Phase3 语义分块启用或裁撤时一并处理。"""

    __tablename__ = "chunk_context"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[str] = tenant_id_column()
    prev_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
    next_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
