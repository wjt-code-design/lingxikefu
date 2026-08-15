"""文档导入 Celery 任务（BU-04）。

- ``import_document_task``：Celery 任务包装，broker 为 Redis（compose 已含 worker 服务）。
- 任务函数内直接调 ``knowledge_import_service.import_document``（同步纯函数），
  因此 **API 降级路径 / 单测可不经 Redis** 直接调用同名函数，行为一致。
"""
from __future__ import annotations

import logging
from uuid import UUID

from app.core.database import SessionLocal
from app.services.knowledge_import_service import ImportError_, import_document
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="knowledge.import_document", bind=True, max_retries=1, default_retry_delay=5)
def import_document_task(self, doc_id: str) -> dict:
    """Celery 任务：导入单个文档。失败重试 1 次（延迟 5s），仍失败则由服务层标 failed。"""
    from sqlalchemy.exc import OperationalError

    db = SessionLocal()
    try:
        doc = import_document(UUID(doc_id), db)
        return {"ok": True, "doc_id": doc_id, "status": doc.status.value}
    except ImportError_ as e:
        # 业务性失败（不可重试）：错误已写入 Document.error，返回失败结果
        logger.warning("文档 %s 导入失败（业务）：%s", doc_id, e)
        return {"ok": False, "doc_id": doc_id, "error": str(e)}
    except OperationalError as e:
        # 数据库抖动才重试（DB 不可达时服务层异常类型为 OperationalError）
        logger.warning("文档 %s 导入遇 DB 抖动，重试", doc_id)
        raise self.retry(exc=e)
    finally:
        db.close()
