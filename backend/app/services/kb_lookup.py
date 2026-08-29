"""KB 查询服务（大扫查优化 O1）：latest KB 定位 + 文档标题批量查询。

下沉动机（Standards 审查）：
- ``_latest_kb_id`` 原属 chat.py 私有函数，sessions.py 横向 ``from app.api.chat import _latest_kb_id``
  属 API 模块间私有符号导入（模块边界异味）——下沉服务层后两个 API 层各自别名导入；
- 文档标题查询原有三份同形拷贝（chat._fetch_doc_titles / sessions._doc_titles /
  knowledge_search.py 既有债）——本模块收敛为单一实现，knowledge_search 留后续批次跟进。

缓存语义（与 chat.py 原实现逐字一致，B4 修复保留）：单租户期单条目缓存
（tenant_middleware 恒 TENANT_DEFAULT，见 6381cd5 裁定；无按键分桶）+ 60s TTL +
线程锁（run_in_threadpool 并发读写）+ 不缓存 None（新建 KB 立即可感知）。
M1（外部审查 2026-08-28）：多租户化前必须改为按 tenant 分桶，否则跨租户泄漏。
"""
from __future__ import annotations

import threading
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.models.knowledge import Document, KnowledgeBase

#: 最新 KB id 缓存（L7 + R-5 + B4 + P1-②）：租户恒为 TENANT_DEFAULT → key 恒定，
#: 单条目即可（天然有界），60s TTL 内复用避免每请求查 DB。
_kb_cache: tuple[float, uuid.UUID] | None = None
_kb_lock = threading.Lock()
_KB_CACHE_TTL = 60.0


def get_latest_kb_id(db: OrmSession) -> uuid.UUID | None:
    """MVP 单知识库：TENANT_DEFAULT 租户最新创建的 KB（单条目 60s TTL 缓存，L7/R-5/B4）。

    P1-②：与全局鉴权一致读 ``settings.TENANT_DEFAULT``，不采信可伪造 ContextVar 租户，
    消除鉴权(default)与 KB 查询(伪造)读写错位。
    """
    tenant = settings.TENANT_DEFAULT
    global _kb_cache
    with _kb_lock:
        now = time.time()
        hit = _kb_cache
        if hit and now - hit[0] < _KB_CACHE_TTL:
            return hit[1]
        kb = db.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == tenant)
            .order_by(KnowledgeBase.created_at.desc())
            .limit(1)
        )
        kb_id = kb.id if kb else None
        # SQLite（测试）下 Uuid 列可能读回 str，统一转 uuid 缓存（生产 PG 本就是 uuid）
        if kb_id is not None and not isinstance(kb_id, uuid.UUID):
            kb_id = uuid.UUID(str(kb_id))
        # 仅在确有 KB 时缓存；无 KB 不缓存 → 新建后立即生效
        if kb_id is not None:
            _kb_cache = (now, kb_id)
        else:
            _kb_cache = None
        return kb_id


def doc_titles(db: OrmSession, doc_ids: set[uuid.UUID]) -> dict[str, str]:
    """批量查文档标题（消息来源唯一真源；空集返回空 dict）。"""
    if not doc_ids:
        return {}
    rows = db.scalars(select(Document).where(Document.id.in_(doc_ids))).all()
    return {str(d.id): d.name for d in rows}
