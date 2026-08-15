"""Knowledge 路由（BU-04 填充）：KB CRUD + 文档上传/列表/删除。

- 全部端点要求 admin（管理后台 / 知识库写操作），非 admin 403。
- 上传：multipart 单文件；sha256 同 KB 去重（幂等返回已有文档）；解析失败（不支持类型/扫描件）
  直接 400 拒绝，不落库（failed 仅留给导入流程中 embedding/Qdrant 失败）。
- 导入调度：优先 Celery 异步（``.delay``），broker 不可达时**显式降级同步**执行
  （行为一致：同一 import_document 函数），不静默丢任务。
- 删除：先清 Qdrant 向量（失败 500 保持可重试，不留脏镜像），再删 PG（FK 级联 chunks）。
"""
from __future__ import annotations

import hashlib
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge import DocumentStatus
from app.repositories.document_repo import (
    DocumentRepository,
    KnowledgeBaseRepository,
)
from app.schemas.knowledge import (
    CreateKBReq,
    DocItem,
    DocumentListResp,
    KBItem,
    KBListResp,
    OkResp,
)
from app.services import vector_service
from app.services.document_service import SUPPORTED_EXTENSIONS, UnsupportedFileError, extract_text
from app.services.vector_service import VectorStoreError

logger = logging.getLogger(__name__)

#: KB 资源路由（/api/v1/knowledge-bases）
router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])
#: 文档独立资源路由（/api/v1/documents/{doc_id}，前端签名仅传 doc_id）
documents_router = APIRouter(tags=["knowledge"])


def _doc_item(doc) -> DocItem:
    return DocItem(
        doc_id=str(doc.id),
        name=doc.name,
        status=doc.status.value,
        chunks=doc.chunk_count,
        error=doc.error,
    )


@router.get("", response_model=KBListResp)
def list_knowledge_bases(
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> KBListResp:
    repo = KnowledgeBaseRepository(db)
    items = [
        KBItem(
            kb_id=str(kb.id),
            name=kb.name,
            doc_count=repo.doc_count(kb.id),
            chunk_count=repo.chunk_count(kb.id),
        )
        for kb in repo.list_all()
    ]
    return KBListResp(items=items)


@router.post("", response_model=KBItem, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(
    req: CreateKBReq,
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> KBItem:
    repo = KnowledgeBaseRepository(db)
    kb = repo.create(name=req.name, description=req.description)
    return KBItem(kb_id=str(kb.id), name=kb.name, doc_count=0, chunk_count=0)


@router.get("/{kb_id}/documents", response_model=DocumentListResp)
def list_documents(
    kb_id: UUID,
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> DocumentListResp:
    kb_repo = KnowledgeBaseRepository(db)
    if not kb_repo.get(kb_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "knowledge base not found")
    docs = DocumentRepository(db).list_by_kb(kb_id)
    return DocumentListResp(items=[_doc_item(d) for d in docs])


@router.post(
    "/{kb_id}/documents",
    response_model=DocItem,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    kb_id: UUID,
    file: UploadFile = File(...),
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> DocItem:
    repo = KnowledgeBaseRepository(db)
    if not repo.get(kb_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "knowledge base not found")

    content = file.file.read()
    if len(content) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")
    if len(content) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file exceeds {settings.MAX_UPLOAD_MB}MB limit",
        )

    filename = file.filename or "unnamed"
    if "." not in filename:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unsupported file type, allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    # sha256 同 KB 去重（幂等：重复上传返回已有文档，不重新导入）
    sha256 = hashlib.sha256(content).hexdigest()
    existing = DocumentRepository(db).get_by_sha256(kb_id, sha256)
    if existing is not None:
        return _doc_item(existing)

    # 解析文本（不支持类型 / 扫描件 PDF 直接 400，不落库）
    try:
        raw_text = extract_text(filename, content)
    except UnsupportedFileError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    doc = DocumentRepository(db).create(
        kb_id=kb_id,
        name=filename,
        sha256=sha256,
        status=DocumentStatus.parsing,
        raw_text=raw_text,
    )

    # 调度导入：优先 Celery 异步，broker 不可达显式降级同步（不静默丢任务）
    try:
        from app.workers.import_worker import import_document_task

        import_document_task.delay(str(doc.id))
    except Exception as e:  # noqa: BLE001 - broker 不可达（kombu 连接失败等）
        logger.warning("Celery 调度失败，降级同步导入（%s）", e)
        from app.services.knowledge_import_service import ImportError_, import_document

        try:
            import_document(doc.id, db)
        except ImportError_ as ie:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(ie)) from ie

    db.refresh(doc)
    return _doc_item(doc)


@documents_router.delete("/documents/{doc_id}", response_model=OkResp)
def delete_document(
    doc_id: UUID,
    _: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OkResp:
    doc_repo = DocumentRepository(db)
    doc = doc_repo.get(doc_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    # 先清向量（失败 500，保持可重试，不留脏镜像），再删 PG（FK 级联 chunks）
    try:
        vector_service.delete_by_doc_id(doc_id)
    except VectorStoreError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e
    doc_repo.delete(doc)
    return OkResp()
