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

from app.api.deps import require_roles
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge import Document, KnowledgeBase
from app.schemas.knowledge_search import (
    KnowledgeSearchHit,
    KnowledgeSearchReq,
    KnowledgeSearchResp,
)
from app.services.retrieval_service import RetrievalError, search_kb
from app.utils.text_splitter import clean_snippet

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/search", response_model=KnowledgeSearchResp)
async def search_knowledge(
    req: KnowledgeSearchReq,
    # B3-5：收紧为 staff 守卫（agent/admin）——本模块 docstring 本就声明「agent 客服用」，
    # 旧实现仅 get_current_user（任意登录角色可检索），与意图漂移；前端唯一调用方是
    # agent 工作台页（KbSearchPage），普通 user 角色从不触达，收紧零回归面。
    payload: dict = Depends(require_roles("agent", "admin")),
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
    # H2 补漏：同步 DB 查询搬 worker 线程
    doc_ids = {uuid.UUID(c.doc_id) for c in chunks}

    def _fetch_meta() -> tuple[dict[str, str], str]:
        # C4（红线⑨）：标题查询与下方 KB 查询同租户口径（KB 那条本就带条件）
        doc_titles = {
            str(d.id): d.name
            for d in db.scalars(
                select(Document).where(
                    Document.id.in_(doc_ids),
                    Document.tenant_id == settings.TENANT_DEFAULT,
                )
            ).all()
        }
        kb = db.scalar(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.tenant_id == settings.TENANT_DEFAULT,
            )
        )
        return doc_titles, (kb.name if kb else "")

    doc_titles, kb_name = await run_in_threadpool(_fetch_meta)

    return KnowledgeSearchResp(
        query=req.query,
        hits=[
            KnowledgeSearchHit(
                chunk_id=c.chunk_id,
                doc_id=c.doc_id,
                doc_title=doc_titles.get(c.doc_id, ""),
                kb_id=c.kb_id,
                kb_name=kb_name,
                snippet=clean_snippet(c.text),
                score=c.score,
                dense_score=c.dense_score,
            )
            for c in chunks
        ],
    )
