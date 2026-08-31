"""答案缓存（T10）：精确归一层（Redis）+ 语义层（Qdrant answer_cache 集合）。

设计（缓存机制设计-2026-08-16 §二/§三 + 审查补充）：
- 混合三层：①精确归一（Redis，改写后 query sha）→ ②语义（Qdrant 检索，阈值+实体锁定+版本）→ ③miss 走 RAG 回填
- **实体锁定**：query 含实体（型号/商品词）时，命中候选必须包含全部实体（防"手机保修"串"冰箱保修"）
- **极性防护**（架构审核债 5-1）：否定/条件翻转问句（能退/不能退、7天内/超过7天）极性词
  集合不一致即 miss——语义阈值挡不住一词之差的翻转，实体锁定在无数体句时恒放行
- **KB 版本失效**：payload 记录 kb_version（KB.updated_at），不一致即 miss 并清理
- **fail-open**：任何异常降级走 RAG（不阻断）；开关 ANSWER_CACHE_ENABLED 一键关闭
- 不缓存内容由调用方过滤（handoff/个人上下文/拒答不 put）
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
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

#: P2-⑨：集合创建一次即记（进程内只建一次，消除每次 get/put 的 get_collections RPC）；
#: 集合若被外部删除，重建依赖 fail-open 的检索降级，不自动追认（符合单进程语义）。
#: L3（外部审查 2026-08-29 核实）：get/put 经 run_in_threadpool 并发进入，加锁双重检查
#: 防并发首调重复 create_collection（Qdrant 对重复建集合抛 already exists），同 kb_lookup._kb_lock 先例。
_ensured = False
_ensure_lock = threading.Lock()


def _reset_ensured() -> None:
    """m2（bughunt-concurrency）：Qdrant 侧失败（集合被 ops 误删 404 等）→ 复位
    ensured，下次 get/put 重新 _ensure_collection 建集合自愈——旧态进程内恒真，
    命中率归零且重启前无自愈。"""
    global _ensured
    with _ensure_lock:
        _ensured = False


def _normalize_key(query: str) -> str:
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()


def _ensure_collection() -> None:
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:  # 双重检查：等锁期间他人已完成
            return
        client = get_qdrant_client()
        names = {c.name for c in client.get_collections().collections}
        if COLLECTION not in names:
            dim = get_embedding_client().dim
            client.create_collection(
                collection_name=COLLECTION,
                vectors_config={"size": dim, "distance": "Cosine"},
            )
            logger.info("创建 answer_cache 集合 (dim=%s)", dim)
        _ensured = True


def _entities_ok(query: str, cached_question: str) -> bool:
    """实体锁定：query 含实体时，缓存问句必须包含全部实体（防串答）。"""
    qe = _extract_entities(query)
    if not qe:
        return True
    return all(e in cached_question for e in qe)


#: 否定/条件极性防护（架构审核债 5-1，2026-08-29）：语义相似但极性相反的问句
#: （"能退"/"不能退"、"7天内"/"超过7天"）余弦挡不住一词之差的翻转，实体锁定在
#: 无数体句时恒放行。极性词集合不一致即判翻转 → miss 走 RAG：误拦只损失一次
#: 缓存命中，漏拦是承诺红线串答，故词表从宽——裸"不"兜底双字否定词覆盖不到的
#: "保修/不保修"类翻转；单字噪声（"不锈钢/不错"）只造成良性 miss。词表只增不减
#: 单调加严（子串词恒共生，等集判定不会因扩表产生新的错误命中）。
_POLARITY_TERMS = (
    "不", "不能", "无法", "不可", "不可以", "不支持", "不提供", "非", "超过", "以外", "之后",
    # M5（bughunt-concurrency）：高频口语否定前缀——"没发货"vs"发货了"一词之差翻转，
    # 旧表缺「没/未/无」致两问极性类均为空集而串答。单字噪声（"无理由/不锈钢"）只造成良性 miss。
    "没", "未", "无", "别", "莫",
)
#: 同义否定 → 规范类（H3 债清偿）："无法/不可/不可以"与"不能"同极性，不归一则
#: 跨家族句子集合永不相等 → 高频否定问句缓存命中率结构性为 0。
_POLARITY_CANON = {"无法": "不能", "不可": "不能", "不可以": "不能"}


def _polarity_classes(text: str) -> frozenset:
    """命中词 → 规范类 → 极大类归并：被同侧其他类包含的短类不计。

    归并是必须的：裸"不"⊂"不能"，含"不能"的句子原始集合恒多出"不"，
    与任何不含裸"不"字面的否定家族（如"无法"）永不等。同极性合并不产生
    新的错误命中（只会回收良性 miss），保持词表"只增不减"不变量。

    M5 扩表（没/未/无/别/莫）后子串消除前置：裸"无"⊂"无法"，命中"无法"的
    句子不再让裸"无"产生独立类（否则"无法退货"{"无","不能"} 与"不能退货"
    {"不能"} 永不等，跨家族归一命中被破坏）。
    """
    hits = [t for t in _POLARITY_TERMS if t in text]
    hits = [t for t in hits if not any(t != o and t in o for o in hits)]
    classes = [_POLARITY_CANON.get(t, t) for t in hits]
    return frozenset(c for c in classes if not any(o != c and c in o for o in classes))


def _polarity_conflict(query: str, cached_question: str) -> bool:
    """极性防护：query 与缓存问句的极性类集合不一致 → 否定/条件翻转，不可命中。"""
    return _polarity_classes(query) != _polarity_classes(cached_question)


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
        try:
            hits = get_qdrant_client().search(**search_kwargs)
        except Exception:
            _reset_ensured()  # m2：集合被外部删除等 → 复位，下次重建自愈
            raise
        if not hits or hits[0].score < settings.ANSWER_CACHE_THRESHOLD:
            return None
        p = hits[0].payload or {}
        if p.get("kb_version") != kb_version:
            return None  # 版本过期 → miss（陈旧答案不返回）
        if kb_id and p.get("kb_id") != str(kb_id):
            return None  # kb 不匹配 → miss（防跨 KB 串答）
        if not _entities_ok(query, p.get("question", "")):
            return None
        if _polarity_conflict(query, p.get("question", "")):
            return None  # 否定/条件翻转（能退/不能退、7天内/超过7天）→ miss，宁可走 RAG
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
        try:
            get_qdrant_client().upsert(
                collection_name=COLLECTION,
                points=[
                    {
                        # M5（外部审查 2026-08-22）：确定性 point id——同一问题+库重复回填时
                        # upsert 覆盖同一点（天然去重）。此前用 uuid4 随机 id，语义层只增不减、
                        # 高频问句反复回填导致集合无界膨胀。（kb_id 已隔离，单租户无需再加租户段）
                        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{kb_id}:{query}")),
                        "vector": vector,
                        "payload": payload,
                    }
                ],
            )
        except Exception:
            _reset_ensured()  # m2：同 get——回填侧失败也复位重建自愈
            raise
        get_redis().set(
            _EXACT_PREFIX + (f"{kb_id}:" if kb_id else "") + _normalize_key(query),
            json.dumps(payload, ensure_ascii=False),
            ex=settings.ANSWER_CACHE_TTL_HOURS * 3600,
        )
    except Exception:  # noqa: BLE001 - fail-open
        logger.exception("answer_cache.put 失败（降级不缓存）")


def evict_stale_kb(kb_id: str, current_version: str) -> None:
    """P2-⑨：KB 版本推进 → 删除该 KB 下旧版本语义缓存点（防集合无淘汰膨胀）。

    挂在知识导入成功钩子上：kb_version 变化后旧答案已不可命中（版本校验 miss），
    这里只是把陈旧点清出集合，控住存量。fail-open：清理失败不影响导入。

    按过滤条件删除（FilterSelector，与 vector_service 同款）：must=kb_id 隔离租户库，
    must_not=当前版本 → 只删该 KB 的旧版本点，单 RPC 完成（无需 scroll 分页收集）。
    """
    if not settings.ANSWER_CACHE_ENABLED:
        return
    try:
        from qdrant_client.http.models import (
            FieldCondition,
            Filter,
            FilterSelector,
            MatchValue,
        )

        _ensure_collection()
        client = get_qdrant_client()
        client.delete(
            collection_name=COLLECTION,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="kb_id", match=MatchValue(value=kb_id))],
                    must_not=[
                        FieldCondition(
                            key="kb_version", match=MatchValue(value=current_version)
                        )
                    ],
                )
            ),
        )
        logger.info("answer_cache 驱逐 kb=%s 旧版本语义缓存点（v!=%s）", kb_id, current_version)
    except Exception:  # noqa: BLE001 - fail-open
        logger.exception("answer_cache.evict_stale_kb 失败（降级不清理）")
