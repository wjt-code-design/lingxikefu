"""知识检索路由（Phase 4）：/api/v1/knowledge/search（agent 客服用，需登录，非 admin）。

- ``search_kb`` 是同步阻塞（embedding）→ ``run_in_threadpool`` 包一层，避免阻塞事件循环；
- 批量查 Document 标题 + KB 名（参照 chat.py _fetch_doc_titles 模式）；
- ``RetrievalError`` → 503「检索不可用」；空结果返回空 hits 不报错。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge import Document, KnowledgeBase
from app.schemas.knowledge_search import (
    KnowledgeSearchHit,
    KnowledgeSearchReq,
    KnowledgeSearchResp,
)
from app.services.retrieval_service import RetrievalError, search_kb

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/search", response_model=KnowledgeSearchResp)
async def search_knowledge(
    req: KnowledgeSearchReq,
    payload: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeSearchResp:
    """按 KB 检索知识切片（agent 客服工作台）。kb_id 必填，query 非空。"""
    if not req.kb_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="kb_id is required",
        )
    try:
        kb_id = uuid.UUID(req.kb_id)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid kb_id",
        ) from None

    try:
        chunks = await run_in_threadpool(search_kb, req.query, kb_id, req.top_k)
    except RetrievalError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="检索不可用",
        ) from e

    if not chunks:
        return KnowledgeSearchResp(query=req.query, hits=[])

    # 批量查 Document 标题（参照 chat.py _fetch_doc_titles）+ KB 名
    doc_ids = {uuid.UUID(c.doc_id) for c in chunks}
    doc_titles = {
        str(d.id): d.name
        for d in db.scalars(select(Document).where(Document.id.in_(doc_ids))).all()
    }
    kb = db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.tenant_id == settings.TENANT_DEFAULT,
        )
    )
    kb_name = kb.name if kb else ""

    return KnowledgeSearchResp(
        query=req.query,
        hits=[
            KnowledgeSearchHit(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                doc_title=doc_titles.get(c.doc_id, ""),
                kb_id=c.kb_id,
                kb_name=kb_name,
                snippet=c.text[:200],
                score=c.score,
                dense_score=c.dense_score,
            )
            for c in chunks
        ],
    )
