"""Knowledge 路由（BU-04 填充）：KB CRUD + 文档上传/列表/删除。

- 全部端点要求 admin（管理后台 / 知识库写操作），非 admin 403。
- 上传：multipart 单文件；sha256 同 KB 去重（幂等返回已有文档）；解析失败（不支持类型/扫描件）
  直接 400 拒绝，不落库（failed 仅留给导入流程中 embedding/Qdrant 失败）。
- 导入调度：优先 Celery 异步（``.delay``），broker 不可达时**后台线程**异步执行
  （不阻塞上传请求线程，状态由前端轮询），不静默丢任务。
- 删除：先清 Qdrant 向量（失败 500 保持可重试，不留脏镜像），再删 PG（FK 级联 chunks）。
- Phase4：KB 创建/删除、文档上传/删除成功路径埋点审计（audit_log，fail-open 不影响主流程）。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.core.database import SessionLocal, get_db
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
from app.services import kb_publish_service, vector_service
from app.services.audit_service import audit_log
from app.services.document_service import SUPPORTED_EXTENSIONS, UnsupportedFileError, extract_text
from app.services.knowledge_import_service import ImportError_, import_document
from app.services.vector_service import VectorStoreError

logger = logging.getLogger(__name__)


#: 并发导入信号量（Bug 修复）：批量上传每文档起一线程，无限制会让 embedding CPU 推理
#: 线程爆炸（N 文档 = N 并发模型推理）。限 4 并发，超出排队（线程阻塞等待），防资源耗尽。
_IMPORT_SEMAPHORE = threading.BoundedSemaphore(4)


def _run_background_import(doc_id: str, *, visible: bool = True, batch_tag: str | None = None) -> None:
    """后台线程执行导入（M9：避免阻塞上传请求线程）；独立 DB 会话；信号量限并发。"""
    with _IMPORT_SEMAPHORE:
        bg_db = SessionLocal()
        try:
            import_document(UUID(doc_id), bg_db, visible=visible, batch_tag=batch_tag)
        except ImportError_:
            logger.warning("后台导入文档 %s 失败（已标 failed）", doc_id)
        except Exception:  # noqa: BLE001
            logger.exception("后台导入文档 %s 异常", doc_id)
        finally:
            bg_db.close()


def _enqueue_background_import(
    doc_id: str, *, visible: bool = True, batch_tag: str | None = None
) -> None:
    threading.Thread(
        target=_run_background_import, args=(doc_id,), kwargs={"visible": visible, "batch_tag": batch_tag}, daemon=True
    ).start()

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
    payload: dict = Depends(require_admin),
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
    payload: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> KBItem:
    repo = KnowledgeBaseRepository(db)
    kb = repo.create(name=req.name, description=req.description)
    # Phase4 审计埋点：KB 创建（resource=knowledge_base）
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="kb.create",
        resource="knowledge_base",
        resource_id=str(kb.id),
    )
    return KBItem(kb_id=str(kb.id), name=kb.name, doc_count=0, chunk_count=0)


@router.get("/{kb_id}/documents", response_model=DocumentListResp)
def list_documents(
    kb_id: UUID,
    payload: dict = Depends(require_admin),
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
    batch_id: str | None = Query(None, max_length=64, description="发布批次 id（可选；带则走 staged 暂存通道）"),
    payload: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> DocItem:
    """上传文档（BU-04）。batch_id（门禁 v2 G2，可选）：不带＝现状直通
    （import_document 默认 visible=True，零变化）；带＝文档 staged 导入
    （visible=False + batch_tag）并记入发布批次（首个上传隐式建行，pending）。"""
    repo = KnowledgeBaseRepository(db)
    if not repo.get(kb_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "knowledge base not found")

    if batch_id is not None:
        batch_id = batch_id.strip()
        if not batch_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "batch_id 不能为空")

    # M6（外部审查 2026-08-22）：分块读取到上限即止——此前先全量 read() 再校验，
    # 传超大文件会先吃满内存才返回 413
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"file exceeds {settings.MAX_UPLOAD_MB}MB limit",
            )
        chunks.append(chunk)
    content = b"".join(chunks)
    if len(content) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty file")

    filename = file.filename or "unnamed"
    if "." not in filename:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unsupported file type, allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    # sha256 同 KB 去重（幂等：重复上传返回已有文档，不重新导入）。
    # 批次通道例外：命中说明该内容已存在（可能已发布可见），不能悄悄挂进 staged 批次
    # （重导入会开"旧内容瞬间消失"窗口），显式 400 让管理员先删后传。
    sha256 = hashlib.sha256(content).hexdigest()
    existing = DocumentRepository(db).get_by_sha256(kb_id, sha256)
    if existing is not None:
        if batch_id is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"文档 {existing.name} 已存在（sha256 去重）；如需纳入批次请先删除后重新上传",
            )
        return _doc_item(existing)

    # 解析文本（不支持类型 / 扫描件 PDF 直接 400，不落库）
    try:
        raw_text = extract_text(filename, content)
    except UnsupportedFileError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    # G2 审查 Important-1：批次可接受性预检前置——拒绝发生在文档落库之前，
    # 避免 400 后留 parsing 僵尸文档（挡 sha256 去重、永不导入）。
    if batch_id is not None:
        try:
            kb_publish_service.ensure_batch_accepts(db, kb_id, batch_id)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    doc = DocumentRepository(db).create(
        kb_id=kb_id,
        name=filename,
        sha256=sha256,
        status=DocumentStatus.parsing,
        raw_text=raw_text,
    )

    if batch_id is not None:
        # 门禁 v2 G2：批次登记（隐式建行/追加 doc_ids）。跨 KB 冲突/唯一索引竞争 → 400。
        try:
            kb_publish_service.upsert_batch_membership(db, kb_id, batch_id, doc.id)
        except ValueError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    # 调度导入：优先 Celery 异步；broker 不可达 → 后台线程异步执行（M9：不阻塞请求）。
    # 批次通道传 staged 参数（visible=False + batch_tag），直通路径不带（零变化）。
    try:
        from app.workers.import_worker import import_document_task

        if batch_id is not None:
            import_document_task.delay(str(doc.id), visible=False, batch_tag=batch_id)
        else:
            import_document_task.delay(str(doc.id))
    except Exception as e:  # noqa: BLE001 - broker 不可达（kombu 连接失败等）
        logger.warning("Celery 调度失败，降级后台线程导入（%s）", e)
        _enqueue_background_import(
            str(doc.id), visible=batch_id is None, batch_tag=batch_id
        )

    db.refresh(doc)
    # Phase4 审计埋点：文档上传成功（resource=document）
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="doc.upload",
        resource="document",
        resource_id=str(doc.id),
        detail=filename,
    )
    return _doc_item(doc)


@documents_router.delete("/documents/{doc_id}", response_model=OkResp)
def delete_document(
    doc_id: UUID,
    payload: dict = Depends(require_admin),
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
    # Phase4 审计埋点：文档删除（resource=document）
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="doc.delete",
        resource="document",
        resource_id=str(doc_id),
        detail=doc.name,
    )
    return OkResp()


@router.delete("/{kb_id}", response_model=OkResp)
def delete_knowledge_base(
    kb_id: UUID,
    payload: dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> OkResp:
    """删除知识库（T4）：清空其下全部文档向量 + PG（documents/chunks 级联删除）。"""
    kb_repo = KnowledgeBaseRepository(db)
    kb = kb_repo.get(kb_id)
    if not kb:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "knowledge base not found")
    docs = DocumentRepository(db).list_by_kb(kb_id)
    # 先清所有文档向量（失败 500 保持可重试），再删 KB（documents CASCADE）
    try:
        for d in docs:
            vector_service.delete_by_doc_id(d.id)
    except VectorStoreError as e:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(e)) from e
    kb_repo.delete(kb)
    # Phase4 审计埋点：KB 删除（resource=knowledge_base）
    audit_log(
        db,
        actor_id=payload["sub"],
        actor_role=payload.get("role"),
        action="kb.delete",
        resource="knowledge_base",
        resource_id=str(kb_id),
        detail=kb.name,
    )
    return OkResp()
