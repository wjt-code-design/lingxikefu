"""FAQ 路由（Phase 4）：/api/v1/faq 帮助中心匿名公开访问（无鉴权）。

返回 tenant 过滤的知识库 + 各 KB 文档清单（status/chunks）。
安全边界（2026-09-27 补漏）：清单只返回名称级信息，且不枚举匿名不可见文档的 id；
原文端点仅放行**已索引且已发布**文档的 raw_text——发布态在 PG 侧以 KBPublishBatch
推导（kb_visibility：无批次=直通导入可见；有批次=最新批次 released 才可见；staged/
评测中/失败/回滚均不可见）。未发布/未索引/无原文/跨租户一律 404，不泄漏存在性
（口径与 chat.py _check_session_access 一致，用 404 防探测）。
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge import Chunk, Document, DocumentStatus, KnowledgeBase
from app.schemas.faq import FaqDocContentResp, FaqDocItem, FaqKbItem, FaqListResp
from app.services import kb_visibility

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
    # B2-3 防 N+1：全部文档一次 in_ 批量取回按 kb_id 分组（旧实现循环内逐 KB 查，
    # 匿名公开端点 → KB 数线性放大查询数）。created_at 倒序在分组内保持（SQL 全局排序）。
    docs_by_kb: dict[Any, list[Document]] = {}
    for d in db.scalars(
        select(Document)
        .where(Document.kb_id.in_(kb_ids), Document.tenant_id == tenant)
        .order_by(Document.created_at.desc())
    ).all():
        docs_by_kb.setdefault(d.kb_id, []).append(d)
    # 发布门禁（2026-09-27 越权补漏）：staged/回滚未发布的草案不得出现在匿名清单
    # （否则 doc_id 可被枚举进而拉原文）。批次判定一条查询搞定（kb_visibility，
    # 内部单次 select KBPublishBatch），禁止逐 doc 查批次。
    all_docs = [d for grouped in docs_by_kb.values() for d in grouped]
    visible = kb_visibility.anonymous_visible_doc_ids(db, [d.id for d in all_docs])
    docs_by_kb = {
        kb_id: [d for d in grouped if d.id in visible]
        for kb_id, grouped in docs_by_kb.items()
    }
    items: list[FaqKbItem] = []
    for kb in kbs:
        docs = docs_by_kb.get(kb.id, [])
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


@router.get("/docs/{doc_id}/content", response_model=FaqDocContentResp)
def get_doc_content(
    doc_id: UUID,
    db: Session = Depends(get_db),
) -> FaqDocContentResp:
    """方案A：政策原文浏览——返回单篇**已索引且已发布**文档的公开原文。

    404 统一口径（不区分「不存在 / 跨租户 / 未索引 / 未发布 / 无原文」）：
    未就绪内容不展示，也不泄漏存在性。发布态以 KBPublishBatch 推导（见
    app/services/kb_visibility.py）——status=indexed 但最新批次非 released
    （staged/评测中/失败/回滚）的草案在这里同样 404，堵「已索引未发布」越权读原文。
    """
    tenant = settings.TENANT_DEFAULT
    row = db.execute(
        select(Document, KnowledgeBase.name)
        .join(KnowledgeBase, KnowledgeBase.id == Document.kb_id)
        .where(
            Document.id == doc_id,
            Document.tenant_id == tenant,
            KnowledgeBase.tenant_id == tenant,
            Document.status == DocumentStatus.indexed,
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在或暂不可读")
    doc, kb_name = row
    # 发布门禁：批次派生不可见（staged/rolled_back 等）→ 404，不用 403（防探测）
    if doc.id not in kb_visibility.anonymous_visible_doc_ids(db, [doc.id]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在或暂不可读")
    if not doc.raw_text:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在或暂不可读")
    return FaqDocContentResp(
        doc_id=str(doc.id),
        name=doc.name,
        kb_name=kb_name,
        status=doc.status.value,
        content=doc.raw_text,
    )
