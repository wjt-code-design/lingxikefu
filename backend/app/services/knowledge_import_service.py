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

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm_clients.embedding import get_embedding_client
from app.models.knowledge import Document, DocumentStatus
from app.repositories.document_repo import (
    ChunkRepository,
    DocumentRepository,
)
from app.services import answer_cache, vector_service
from app.services.kb_lookup import kb_version_str
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
    # P4：清旧失败 → 中止导入标 failed。旧版"清失败继续导入"会产生孤儿向量
    # （新 chunk 写入、旧向量残留）→ 检索命中新旧混杂内容；宁可失败重试也不残留脏数据。
    try:
        vector_service.delete_by_doc_id(doc_id)
    except VectorStoreError as e:
        doc_repo.set_status(doc, DocumentStatus.failed, "无法清理该文档旧向量，导入中止")
        logger.warning("清理文档 %s 旧向量失败，中止导入", doc_id)
        raise ImportError_(f"无法清理该文档旧向量（{e}），导入中止（请稍后重试）") from e
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
    """P2-⑨：导入成功后按新 kb_version 清理旧语义缓存点（fail-open，不阻塞导入）。

    三期 3：版本公式收敛为 kb_lookup.kb_version_str 单一真源（chat/评测门禁同式，
    此前本函数内联同式拷贝）。fail-open 边界不变：版本计算失败 → version=None →
    仅驱逐缓存步骤跳过，_check_quick_coverage 照常收到 None（不进入受控状态）。
    """
    version: str | None = None
    try:
        version = kb_version_str(db, kb_id)
        answer_cache.evict_stale_kb(str(kb_id), version)
    except Exception:  # noqa: BLE001 - fail-open：缓存清理失败不影响导入结果
        logger.exception("KB 版本推进缓存清理失败（不阻塞导入）")
    # P4：快捷话术与 KB 双源漂移告警（fail-open，不阻塞导入）；version 供 5-2 门禁记录
    _check_quick_coverage(db, kb_id, version)


def _check_quick_coverage(db: Session, kb_id: UUID, kb_version: str | None) -> None:
    """P4：快捷预置话术 vs KB 内容覆盖校验——漂移告警 + 5-2 失效面门禁（不阻塞导入）。

    - 告警：无覆盖依据的话术记 warning（不阻断），提示"该话题只有话术没有文档"，
      运营据此补录知识或更新话术；
    - 门禁（架构审核债 5-2）：check_kb_coverage 通过且 kb_version 可锚定时记录通过版本，
      chat 端 quick 短路据此放行；未通过不记录 → 新版本 quick 回落 RAG（防陈旧话术）。
      kb_version 为 None（版本计算失败）时只告警、不进入受控状态（fail-open）。
    - 文本过大（>2M 字符）跳过扫描：无法核验 → 新版本不获通过记录，quick 回落 RAG
      （宁可损失秒回也不放行未核验话术），warning 提示运营关注。
    """
    try:
        from app.services import quick_answers

        texts = db.scalars(
            select(Document.raw_text).where(
                Document.kb_id == kb_id,
                Document.status == "indexed",
                Document.raw_text.isnot(None),
            )
        ).all()
        blob = "".join(texts or [])
        if len(blob) > 2_000_000:
            logger.warning(
                "KB %s 文本过大（%s 字符），跳过快捷话术覆盖校验——该版本（%s）quick 话术回落 RAG",
                kb_id,
                len(blob),
                kb_version,
            )
            return
        uncovered = quick_answers.uncovered_questions(blob)
        if uncovered:
            logger.warning(
                "快捷话术与 KB 漂移：%s 个快捷问题在 KB 中无覆盖依据（建议补录知识或更新话术）：%s",
                len(uncovered),
                "；".join(uncovered),
            )
        # 5-2：通过则记录该 kb_version（quick 放行锚点）；未通过不记录 → chat 端禁用 quick。
        # 与上面告警各扫一遍 blob：告警按"任一话题未覆盖"报，门禁按占比判——语义不同分属两层。
        quick_answers.check_kb_coverage(blob, kb_version)
    except Exception:  # noqa: BLE001 - fail-open：校验失败不影响导入结果
        logger.exception("快捷话术覆盖校验失败（不阻塞导入）")
