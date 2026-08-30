"""向量写入层（BU-04 写入侧；检索侧 BU-05 补充）。

- ``get_qdrant_client``：进程内单例，懒建连接（import 不触发网络）。
- ``ensure_collection``：集合不存在则按当前 embedding 维度创建；已存在且维度不符 → 拒绝
  （防换 provider 后误写旧集合，维度不同 = 语义空间不同，必须重建索引）。
- ``upsert_document`` / ``delete_by_doc_id``：BU-04 导入 / 删除文档时写 Qdrant。
- 所有错误抛 ``VectorStoreError``（fail-closed）：Qdrant 不可达绝不静默降级成"成功"。
"""
from __future__ import annotations

import logging
import threading
import time
from functools import lru_cache
from uuid import NAMESPACE_DNS, UUID, uuid5

from app.core.config import settings
from app.llm_clients.embedding import get_embedding_client
from app.services.sparse_util import text_to_sparse

logger = logging.getLogger(__name__)


def get_collection_name() -> str:
    """当前生效的 Qdrant 集合名：hybrid 用专用集合（named dense+sparse），否则纯 dense 旧集合。"""
    return settings.QDRANT_COLLECTION_HYBRID if settings.RAG_ENABLE_HYBRID else settings.QDRANT_COLLECTION

#: 集合名列表缓存（L7）：维度不变，60s 内复用，避免每次 upsert/delete 都 get_collections。
_COLLECTIONS_CACHE: tuple[float, set[str]] | None = None
_COLLECTIONS_TTL = 60.0
#: 并发首调防重复 create（同 answer_cache/_kb_lock 先例）：导入多线程同时 upsert 时
#: check-then-create 会撞 Qdrant already exists → 单次导入整批 FAIL-IMPORT（Bug A 同族教训）。
_ensure_lock = threading.Lock()


def _list_collections() -> set[str]:
    global _COLLECTIONS_CACHE
    now = time.time()
    if _COLLECTIONS_CACHE and now - _COLLECTIONS_CACHE[0] < _COLLECTIONS_TTL:
        return _COLLECTIONS_CACHE[1]
    names = {c.name for c in get_qdrant_client().get_collections().collections}
    _COLLECTIONS_CACHE = (now, names)
    return names


def _point_id(doc_id: UUID, idx: int) -> str:
    """稳定派生 point id：Qdrant 只接受纯 UUID 或非负整数，不能拼 "doc_id:idx"。

    uuid5(doc_id, idx) 确定性生成 → 同文档重跑导入幂等（同 id 覆盖），
    且可反查溯源（payload 中 doc_id/idx 已冗余存，无需从 id 反解）。
    """
    return str(uuid5(NAMESPACE_DNS, f"{doc_id}:{idx}"))


class VectorStoreError(Exception):
    """向量库操作失败（导入应据此回滚并标记文档 failed）。"""


def _make_client():
    try:
        from qdrant_client import QdrantClient
    except ImportError as e:  # pragma: no cover - 环境依赖
        raise VectorStoreError(
            f"qdrant-client 未安装：pip install qdrant-client（缺失原始错误: {e}）"
        ) from e
    return QdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY or None,
        timeout=10,
    )


@lru_cache(maxsize=1)
def get_qdrant_client():
    """进程内单例（对齐 embedding/quota 的单例约定，避免每次请求重连）。"""
    return _make_client()


def ensure_collection() -> int:
    """确保 Qdrant 集合存在且维度与当前 embedding 一致，返回维度。

    hybrid 模式创建 named vectors（dense+sparse），纯 dense 模式维持旧结构。
    check-then-create 全程持 _ensure_lock（并发首调防重复建集合，见其注释）。
    """
    dim = get_embedding_client().dim
    name = get_collection_name()

    def _locked() -> int:
        # global 必须声明在本函数（Bug A：赋值所在函数无声明会静默变局部绑定，模块级缓存不刷新）
        global _COLLECTIONS_CACHE
        try:
            client = get_qdrant_client()
            if name not in _list_collections():
                if settings.RAG_ENABLE_HYBRID:
                    client.create_collection(
                        collection_name=name,
                        vectors_config={"dense": {"size": dim, "distance": "Cosine"}},
                        sparse_vectors_config={"sparse": {}},
                    )
                else:
                    client.create_collection(
                        collection_name=name,
                        vectors_config={"size": dim, "distance": "Cosine"},
                    )
                logger.info("创建 Qdrant 集合 %s (dim=%s, hybrid=%s)", name, dim, settings.RAG_ENABLE_HYBRID)
                # Bug A（2026-08-27 修复）：创建成功后必须刷新集合缓存——否则 60s TTL 内
                # 紧接的"以为集合还不存在"的 ensure 会重复 PUT create → 409（eval 导入 12 文档 FAIL-IMPORT）
                _COLLECTIONS_CACHE = None
                return dim
            info = client.get_collection(name)
            vector_params = info.config.params.vectors
            # named vectors（hybrid：{"dense": {...}, "sparse": {...}}）→ 取 dense 维度；纯 dense → 直接 .size
            if isinstance(vector_params, dict):
                existing = vector_params.get("dense").size if vector_params.get("dense") else None
            else:
                existing = vector_params.size if hasattr(vector_params, "size") else None
            if existing != dim:
                raise VectorStoreError(
                    f"Qdrant 集合 {name} 维度 {existing} != 当前 embedding 维度 {dim}，"
                    f"换 embedding provider 需重建集合（QDRANT_COLLECTION 应区分维度）"
                )
            return dim
        except VectorStoreError:
            raise
        except Exception as e:  # noqa: BLE001 - 统一包装为领域错误
            # P2-④：不向调用方泄漏内部 QDRANT_URL；详情走日志
            logger.exception("ensure_collection 失败（qdrant url=%s）", settings.QDRANT_URL)
            raise VectorStoreError(f"Qdrant 不可达/操作失败: {e}") from e

    with _ensure_lock:
        return _locked()


def upsert_document(
    doc_id: UUID,
    kb_id: UUID,
    texts: list[str],
    vectors: list[list[float]],
    visible: bool = True,
    batch_tag: str | None = None,
) -> int:
    """把文档全部切片写入 Qdrant，返回写入条数。

    payload 含 chunk_id/doc_id/kb_id/tenant_id/idx/text/visible，检索与来源溯源（BU-07）依赖它。
    visible（门禁 v2 G1）：False = staged 未发布（检索过滤不可见），发布=翻转 payload；
    默认 True——smoke/单文档直传/评测路径零改动豁免。
    batch_tag（门禁 v2 G2）：staged 批次标记（=batch_id），发布翻转按它一次 filter
    set_payload（不变式：batch_tag 非空 ⇔ visible=False staged 期）；直通路径恒 None。
    """
    if len(texts) != len(vectors):
        raise VectorStoreError(
            f"texts({len(texts)}) 与 vectors({len(vectors)}) 数量不一致，拒绝写入"
        )
    dim = ensure_collection()
    for v in vectors:
        if len(v) != dim:
            raise VectorStoreError(
                f"向量维度 {len(v)} != 集合维度 {dim}，可能 embedding 配置不一致"
            )
    name = get_collection_name()
    # point id 用 uuid5 稳定派生（合法 UUID、去重幂等、可溯源），chunk_id 同值
    if settings.RAG_ENABLE_HYBRID:
        # hybrid：named vectors（dense + sparse bigram），sparse 检索词面匹配补 dense 语义短板
        points = [
            {
                "id": _point_id(doc_id, idx),
                "vector": {
                    "dense": vectors[idx],
                    "sparse": text_to_sparse(texts[idx]),
                },
                "payload": {
                    "chunk_id": _point_id(doc_id, idx),
                    "doc_id": str(doc_id),
                    "kb_id": str(kb_id),
                    "tenant_id": settings.TENANT_DEFAULT,
                    "idx": idx,
                    "text": texts[idx],
                    "visible": visible,
                    "batch_tag": batch_tag,
                },
            }
            for idx in range(len(texts))
        ]
    else:
        points = [
            {
                "id": _point_id(doc_id, idx),
                "vector": vectors[idx],
                "payload": {
                    "chunk_id": _point_id(doc_id, idx),
                    "doc_id": str(doc_id),
                    "kb_id": str(kb_id),
                    "tenant_id": settings.TENANT_DEFAULT,
                    "idx": idx,
                    "text": texts[idx],
                    "visible": visible,
                    "batch_tag": batch_tag,
                },
            }
            for idx in range(len(texts))
        ]
    try:
        get_qdrant_client().upsert(collection_name=name, points=points)
    except Exception as e:  # noqa: BLE001
        raise VectorStoreError(f"Qdrant upsert 失败: {e}") from e
    return len(points)


def delete_by_doc_id(doc_id: UUID) -> None:
    """删除某文档的全部向量（导入失败回滚 / 删除文档时调用）。"""
    name = get_collection_name()
    try:
        client = get_qdrant_client()
        # 先确认集合存在，不存在则无事可删（幂等）；集合名走本地缓存（L7）
        if name in _list_collections():
            from qdrant_client.http.models import (
                FieldCondition,
                Filter,
                FilterSelector,
                MatchValue,
            )

            client.delete(
                collection_name=name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="doc_id", match=MatchValue(value=str(doc_id))
                            )
                        ]
                    )
                ),
            )
    except Exception as e:  # noqa: BLE001
        raise VectorStoreError(f"Qdrant 删除文档向量失败: {e}") from e


def set_visible_by_doc_ids(
    doc_ids: list[UUID] | list[str], visible: bool, batch_tag: str | None = None
) -> None:
    """批量翻转文档全部 points 的 visible payload（门禁 v2 G2 发布/回滚，不重嵌入）。

    - 发布（visible=True）：按 ``batch_tag`` 一次 filter set_payload 命中整批 points
      （staged 导入时已写入 batch_tag=batch_id），同时把 batch_tag 清空——
      不变式：payload.batch_tag 非空 ⇔ staged 未发布；故必须提供 batch_tag，
      缺失即抛 ValueError（防误翻全库）。
    - 回滚（visible=False）：batch_tag 已在发布时清空，改按 ``doc_id ∈ doc_ids``
      （MatchAny，单次调用）精确翻转，并重写 batch_tag 恢复 staged 标记
      （re-publish 可再次按 batch_tag 翻转；doc 数量级几十，单 filter 可承受）。

    集合不存在 → 静默返回（幂等，同 delete_by_doc_id）；失败抛 VectorStoreError
    （fail-closed，调用方保持批次状态可重试）。
    """
    if visible and not batch_tag:
        # 参数校验在 try 外：这是调用方契约错误，不得被包装成 VectorStoreError
        raise ValueError("发布翻转必须提供 batch_tag（按批次 filter 命中，防误翻全库）")
    name = get_collection_name()
    try:
        client = get_qdrant_client()
        if name not in _list_collections():
            return
        from qdrant_client.http.models import (
            FieldCondition,
            Filter,
            FilterSelector,
            MatchAny,
            MatchValue,
        )

        if visible:
            payload = {"visible": True, "batch_tag": None}
            selector = FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="batch_tag", match=MatchValue(value=batch_tag))]
                )
            )
        else:
            payload = {"visible": False, "batch_tag": batch_tag}
            selector = FilterSelector(
                filter=Filter(
                    must=[
                        FieldCondition(
                            key="doc_id",
                            match=MatchAny(any=[str(d) for d in doc_ids]),
                        )
                    ]
                )
            )
        client.set_payload(collection_name=name, payload=payload, points=selector)
    except Exception as e:  # noqa: BLE001
        raise VectorStoreError(f"Qdrant 翻转 visible 失败: {e}") from e
