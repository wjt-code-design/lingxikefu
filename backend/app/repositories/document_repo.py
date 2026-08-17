"""知识库 / 文档 / 切片仓储（BU-04）。单租户：查询显式带 tenant_id。"""
from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.knowledge import Chunk, Document, DocumentStatus, KnowledgeBase


class KnowledgeBaseRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, kb_id: UUID) -> KnowledgeBase | None:
        return (
            self.db.query(KnowledgeBase)
            .filter_by(id=kb_id, tenant_id=settings.TENANT_DEFAULT)
            .first()
        )

    def list_all(self) -> list[KnowledgeBase]:
        return (
            self.db.query(KnowledgeBase)
            .filter_by(tenant_id=settings.TENANT_DEFAULT)
            .order_by(KnowledgeBase.created_at.desc())
            .all()
        )

    def create(self, *, name: str, description: str | None = None) -> KnowledgeBase:
        kb = KnowledgeBase(name=name, description=description)
        self.db.add(kb)
        self.db.commit()
        self.db.refresh(kb)
        return kb

    def delete(self, kb: KnowledgeBase) -> None:
        """级联删除由 DB FK（documents/chunks ON DELETE CASCADE）负责。"""
        self.db.delete(kb)
        self.db.commit()

    def doc_count(self, kb_id: UUID) -> int:
        return (
            self.db.query(Document)
            .filter_by(kb_id=kb_id, tenant_id=settings.TENANT_DEFAULT)
            .count()
        )

    def chunk_count(self, kb_id: UUID) -> int:
        return (
            self.db.query(Chunk)
            .filter_by(kb_id=kb_id, tenant_id=settings.TENANT_DEFAULT)
            .count()
        )


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, doc_id: UUID) -> Document | None:
        return (
            self.db.query(Document)
            .filter_by(id=doc_id, tenant_id=settings.TENANT_DEFAULT)
            .first()
        )

    def get_by_sha256(self, kb_id: UUID, sha256: str) -> Document | None:
        """同 KB 内 sha256 去重（跨 KB 允许同名/同内容，避免误伤）。"""
        return (
            self.db.query(Document)
            .filter_by(kb_id=kb_id, sha256=sha256, tenant_id=settings.TENANT_DEFAULT)
            .first()
        )

    def list_by_kb(self, kb_id: UUID) -> list[Document]:
        return (
            self.db.query(Document)
            .filter_by(kb_id=kb_id, tenant_id=settings.TENANT_DEFAULT)
            .order_by(Document.created_at.desc())
            .all()
        )

    def create(
        self,
        *,
        kb_id: UUID,
        name: str,
        sha256: str,
        status: DocumentStatus = DocumentStatus.parsing,
        raw_text: str | None = None,
    ) -> Document:
        doc = Document(
            kb_id=kb_id,
            name=name,
            sha256=sha256,
            status=status,
            raw_text=raw_text,
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        return doc

    def set_status(self, doc: Document, status: DocumentStatus, error: str | None = None) -> None:
        doc.status = status
        doc.error = error
        doc.chunk_count = self.db.query(Chunk).filter_by(doc_id=doc.id).count()  # 同步真实块数
        self.db.commit()

    def mark_indexed(self, doc: Document, chunk_count: int) -> None:
        doc.status = DocumentStatus.indexed
        doc.error = None
        doc.chunk_count = chunk_count
        self.db.commit()

    def delete(self, doc: Document) -> None:
        self.db.delete(doc)
        self.db.commit()


class ChunkRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def insert_all(self, doc_id: UUID, kb_id: UUID, texts: list[str]) -> list[Chunk]:
        """批量插入切片，返回带 id 的 Chunk 列表（供向量 payload 用）。"""
        from app.models.base import Base  # noqa: F401  # 确保模型已注册

        chunks = [
            Chunk(
                doc_id=doc_id,
                kb_id=kb_id,
                idx=i,
                text=text,
                hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:64],
            )
            for i, text in enumerate(texts)
        ]
        self.db.add_all(chunks)
        self.db.commit()
        for c in chunks:
            self.db.refresh(c)
        return chunks

    def delete_by_doc(self, doc_id: UUID) -> None:
        self.db.query(Chunk).filter_by(doc_id=doc_id).delete(synchronize_session=False)
        self.db.commit()

    def count_by_doc(self, doc_id: UUID) -> int:
        return self.db.query(Chunk).filter_by(doc_id=doc_id).count()
