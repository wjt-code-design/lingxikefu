"""知识库导入编排（BU-04 核心）。状态机：
``parsing → embedding → indexed``；任一步失败 → ``failed``（可操作错误信息入库）。

导入流程（单文档幂等 + 失败回滚）：
1. 解析文本在上传时已完成（``Document.raw_text``），此处直接读取；
2. 切片（``text_splitter``）；
3. 向量化（embedding client，批处理）；
4. 写 PG ``chunks`` + 写 Qdrant 向量；
5. 标 ``indexed``。

回滚语义：任一环节失败 → 清 PG chunks + 幂等清 Qdrant 该文档向量 + 文档标 ``failed``
（error 为可操作信息）。**不残留半成品向量**（防"假索引"：有 chunk 记录但向量缺失）。

幂等：重复执行同一 doc_id 先清旧 chunks + 旧向量（worker 重试 / 重新导入安全）。

本函数为同步纯函数（自带 Session 由调用方传入），Celery worker 与 API 降级路径共用；
测试不依赖 Redis/Celery，直接调用本函数 + mock embedding / vector 层。
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm_clients.embedding import get_embedding_client
from app.models.knowledge import Document, DocumentStatus
from app.repositories.document_repo import (
    ChunkRepository,
    DocumentRepository,
)
from app.services import answer_cache, vector_service
from app.services.vector_service import VectorStoreError
from app.utils.text_splitter import split_text

logger = logging.getLogger(__name__)


_STALE_ERROR = "导入中断（进程异常退出），请重新上传"


def recover_stale_imports(db: Session) -> int:
    """启动恢复（第6组项2）：把滞留在 parsing/embedding 的文档标记为 failed。

    导入由 daemon 线程执行，进程被强杀时线程随进程终止，文档会永久卡在中间态。
    进程重新启动时调用本函数（此时必无进行中的导入），把这些文档置为 failed，
    消除"文档永久卡"窗口；幂等，且不误伤 indexed/failed。

    返回被恢复（置 failed）的文档数。
    """
    res = db.execute(
        update(Document)
        .where(
            Document.status.in_(
                [DocumentStatus.parsing.value, DocumentStatus.embedding.value]
            )
        )
        .values(status=DocumentStatus.failed, error=_STALE_ERROR)
    )
    db.commit()
    return res.rowcount or 0


class ImportError_(Exception):
    """文档导入失败（错误信息会写入 Document.error 并标 failed）。"""


def import_document(doc_id: UUID, db: Session):
    """导入单个文档，返回更新后的 Document。任一步失败标 failed 并回滚已写数据。"""
    doc_repo = DocumentRepository(db)
    chunk_repo = ChunkRepository(db)
    doc = doc_repo.get(doc_id)
    if doc is None:
        raise ImportError_(f"文档不存在: {doc_id}")

    # --- 幂等：清旧数据（worker 重试 / 重复导入安全） ---
    try:
        vector_service.delete_by_doc_id(doc_id)
    except VectorStoreError:
        # 清理失败不中断：后续 upsert 失败同样会标 failed，最终状态一致
        logger.warning("清理文档 %s 旧向量失败（继续导入）", doc_id)
    chunk_repo.delete_by_doc(doc_id)

    # --- 状态机：进入 embedding ---
    doc_repo.set_status(doc, DocumentStatus.embedding)

    try:
        if not doc.raw_text:
            raise ImportError_("文档无可解析文本（可能为扫描件 PDF 或空文件）")

        chunks = split_text(
            doc.raw_text,
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
        )
        if not chunks:
            raise ImportError_("文档切片结果为空（文本过短或无有效内容）")

        # 向量化（批处理；embedding 失败无任何写入，直接回滚）
        vectors = get_embedding_client().embed(chunks)

        # 写 PG chunks（先落库，Qdrant 失败时据此回滚）
        chunk_rows = chunk_repo.insert_all(doc.id, doc.kb_id, chunks)

        # 写 Qdrant（失败 → 回滚清 PG chunks + 标 failed）
        try:
            vector_service.upsert_document(doc.id, doc.kb_id, chunks, vectors)
        except VectorStoreError as e:
            chunk_repo.delete_by_doc(doc.id)
            raise ImportError_(str(e)) from e

        doc_repo.mark_indexed(doc, len(chunk_rows))
        logger.info("文档 %s 导入完成: %s chunks", doc.name, len(chunk_rows))
        # P2-⑨：KB 版本推进 → 清理该 KB 的旧版本语义缓存点（fail-open，不阻塞导入）
        _evict_stale_cache_after_import(db, doc.kb_id)
        return doc

    except ImportError_ as e:
        doc_repo.set_status(doc, DocumentStatus.failed, error=str(e))
        raise
    except Exception as e:  # noqa: BLE001 - 未预期错误统一标 failed，不静默
        logger.exception("文档 %s 导入异常", doc_id)
        doc_repo.set_status(doc, DocumentStatus.failed, error=f"导入异常: {e}")
        raise ImportError_(f"导入异常: {e}") from e


def _evict_stale_cache_after_import(db: Session, kb_id: UUID) -> None:
    """P2-⑨：导入成功后按新 kb_version 清理旧语义缓存点（fail-open，不阻塞导入）。"""
    try:
        cnt = (
            db.scalar(
                select(func.count(Document.id)).where(
                    Document.kb_id == kb_id,
                    Document.status == "indexed",
                )
            )
            or 0
        )
        latest = db.scalar(select(func.max(Document.created_at)).where(Document.kb_id == kb_id))
        version = f"{cnt}:{latest.isoformat() if latest else ''}"
        answer_cache.evict_stale_kb(str(kb_id), version)
    except Exception:  # noqa: BLE001 - fail-open：缓存清理失败不影响导入结果
        logger.exception("KB 版本推进缓存清理失败（不阻塞导入）")
