"""答案缓存（T10）：精确归一层（Redis）+ 语义层（Qdrant answer_cache 集合）。

设计（缓存机制设计-2026-08-16 §二/§三 + 审查补充）：
- 混合三层：①精确归一（Redis，改写后 query sha）→ ②语义（Qdrant 检索，阈值+实体锁定+版本）→ ③miss 走 RAG 回填
- **实体锁定**：query 含实体（型号/商品词）时，命中候选必须包含全部实体（防"手机保修"串"冰箱保修"）
- **KB 版本失效**：payload 记录 kb_version（KB.updated_at），不一致即 miss 并清理
- **fail-open**：任何异常降级走 RAG（不阻断）；开关 ANSWER_CACHE_ENABLED 一键关闭
- 不缓存内容由调用方过滤（handoff/个人上下文/拒答不 put）
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime

from app.core.config import settings
from app.core.redis_client import get_redis
from app.services.query_rewrite import _extract_entities
from app.services.retrieval_service import BGE_QUERY_PREFIX, get_embedding_client
from app.services.vector_service import get_qdrant_client

logger = logging.getLogger(__name__)

COLLECTION = "answer_cache"
_EXACT_PREFIX = "answer_cache_exact:"


def _normalize_key(query: str) -> str:
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()


def _ensure_collection() -> None:
    client = get_qdrant_client()
    names = {c.name for c in client.get_collections().collections}
    if COLLECTION not in names:
        dim = get_embedding_client().dim
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config={"size": dim, "distance": "Cosine"},
        )
        logger.info("创建 answer_cache 集合 (dim=%s)", dim)


def _entities_ok(query: str, cached_question: str) -> bool:
    """实体锁定：query 含实体时，缓存问句必须包含全部实体（防串答）。"""
    qe = _extract_entities(query)
    if not qe:
        return True
    return all(e in cached_question for e in qe)


def get(query: str, kb_version: str | None, kb_id: str | None = None) -> dict | None:
    """缓存命中（精确→语义）。返回 payload 或 None（fail-open 恒不抛）。

    kb_id 隔离（审查修复）：精确层 key 与语义层检索均按 kb_id 过滤，
    防多 KB 场景跨 KB 串答；kb_id 为空（旧调用方）则退化为全库检索（安全由版本兜底）。
    """
    if not settings.ANSWER_CACHE_ENABLED:
        return None
    try:
        _ensure_collection()
        # 1) 精确层（Redis 快路径）：key 含 kb_id，跨 KB 天然隔离
        r = get_redis()
        key = _EXACT_PREFIX + (f"{kb_id}:" if kb_id else "") + _normalize_key(query)
        exact = r.get(key)
        if exact:
            payload = json.loads(exact)
            if payload.get("kb_version") == kb_version and (kb_id is None or payload.get("kb_id") == str(kb_id)):
                return payload
            r.delete(key)  # 版本/kb 过期清理
        # 2) 语义层（Qdrant + 阈值 + 实体锁定 + 版本 + kb 过滤）
        vector = get_embedding_client().embed([BGE_QUERY_PREFIX + query])[0]
        search_kwargs: dict = dict(collection_name=COLLECTION, query_vector=vector, limit=1)
        if kb_id:
            from qdrant_client.http.models import FieldCondition, Filter, MatchValue

            search_kwargs["query_filter"] = Filter(
                must=[FieldCondition(key="kb_id", match=MatchValue(value=str(kb_id)))]
            )
        hits = get_qdrant_client().search(**search_kwargs)
        if not hits or hits[0].score < settings.ANSWER_CACHE_THRESHOLD:
            return None
        p = hits[0].payload or {}
        if p.get("kb_version") != kb_version:
            return None  # 版本过期 → miss（陈旧答案不返回）
        if kb_id and p.get("kb_id") != str(kb_id):
            return None  # kb 不匹配 → miss（防跨 KB 串答）
        if not _entities_ok(query, p.get("question", "")):
            return None
        return p
    except Exception:  # noqa: BLE001 - fail-open
        logger.exception("answer_cache.get 失败（降级走 RAG）")
        return None


def put(query: str, answer: str, sources: list[dict], doc_ids: list[str], kb_version: str | None, kb_id: str | None = None) -> None:
    """回填缓存（Qdrant payload 主写 + Redis 精确层副本，TTL 兜底）。fail-open。

    kb_id 一并写入 payload（审查修复）：语义层检索与精确层 key 均可按 kb 隔离。
    """
    if not settings.ANSWER_CACHE_ENABLED:
        return
    try:
        _ensure_collection()
        payload = {
            "question": query,
            "answer": answer,
            "sources": sources,
            "doc_ids": [str(d) for d in doc_ids],
            "kb_version": kb_version,
            "kb_id": str(kb_id) if kb_id else "",
            "created_at": datetime.now().isoformat(),
        }
        vector = get_embedding_client().embed([BGE_QUERY_PREFIX + query])[0]
        get_qdrant_client().upsert(
            collection_name=COLLECTION,
            points=[
                {
                    "id": str(uuid.uuid4()),
                    "vector": vector,
                    "payload": payload,
                }
            ],
        )
        get_redis().set(
            _EXACT_PREFIX + (f"{kb_id}:" if kb_id else "") + _normalize_key(query),
            json.dumps(payload, ensure_ascii=False),
            ex=settings.ANSWER_CACHE_TTL_HOURS * 3600,
        )
    except Exception:  # noqa: BLE001 - fail-open
        logger.exception("answer_cache.put 失败（降级不缓存）")
