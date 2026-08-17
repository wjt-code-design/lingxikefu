"""FAQ 路由（Phase 4）：/api/v1/faq 帮助中心匿名公开访问（无鉴权）。

返回 tenant 过滤的知识库 + 各 KB 文档清单（status/chunks）。
安全边界：只返回文档名称级信息，**禁止**返回 chunk 全文。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge import Chunk, Document, KnowledgeBase
from app.schemas.faq import FaqDocItem, FaqKbItem, FaqListResp

router = APIRouter(prefix="/faq", tags=["faq"])


@router.get("", response_model=FaqListResp)
def list_faq(db: Session = Depends(get_db)) -> FaqListResp:
    """列出 tenant 下全部知识库 + 文档清单（名称级，无 chunk 全文）。"""
    tenant = settings.TENANT_DEFAULT
    kbs = db.scalars(
        select(KnowledgeBase)
        .where(KnowledgeBase.tenant_id == tenant)
        .order_by(KnowledgeBase.created_at.desc())
    ).all()
    if not kbs:
        return FaqListResp(items=[])

    kb_ids = [kb.id for kb in kbs]
    chunk_counts = dict(
        db.execute(
            select(Chunk.kb_id, func.count(Chunk.id))
            .where(Chunk.kb_id.in_(kb_ids), Chunk.tenant_id == tenant)
            .group_by(Chunk.kb_id)
        ).all()
    )
    items: list[FaqKbItem] = []
    for kb in kbs:
        docs = db.scalars(
            select(Document)
            .where(Document.kb_id == kb.id, Document.tenant_id == tenant)
            .order_by(Document.created_at.desc())
        ).all()
        items.append(
            FaqKbItem(
                kb_id=str(kb.id),
                kb_name=kb.name,
                description=kb.description,
                doc_count=len(docs),
                chunk_count=chunk_counts.get(kb.id, 0),
                docs=[
                    FaqDocItem(
                        doc_id=str(d.id),
                        name=d.name,
                        status=d.status.value,
                        chunks=d.chunk_count,
                    )
                    for d in docs
                ],
            )
        )
    return FaqListResp(items=items)
