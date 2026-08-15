"""向量写入层（BU-04 写入侧；检索侧 BU-05 补充）。

- ``get_qdrant_client``：进程内单例，懒建连接（import 不触发网络）。
- ``ensure_collection``：集合不存在则按当前 embedding 维度创建；已存在且维度不符 → 拒绝
  （防换 provider 后误写旧集合，维度不同 = 语义空间不同，必须重建索引）。
- ``upsert_document`` / ``delete_by_doc_id``：BU-04 导入 / 删除文档时写 Qdrant。
- 所有错误抛 ``VectorStoreError``（fail-closed）：Qdrant 不可达绝不静默降级成"成功"。
"""
from __future__ import annotations

import logging
from functools import lru_cache
from uuid import UUID, NAMESPACE_DNS, uuid5

from app.core.config import settings
from app.llm_clients.embedding import get_embedding_client

logger = logging.getLogger(__name__)


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
    """确保 Qdrant 集合存在且维度与当前 embedding 一致，返回维度。"""
    dim = get_embedding_client().dim
    name = settings.QDRANT_COLLECTION
    try:
        client = get_qdrant_client()
        collections = [c.name for c in client.get_collections().collections]
        if name not in collections:
            client.create_collection(
                collection_name=name,
                vectors_config={"size": dim, "distance": "Cosine"},
            )
            logger.info("创建 Qdrant 集合 %s (dim=%s)", name, dim)
            return dim
        info = client.get_collection(name)
        vector_params = info.config.params.vectors
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
        raise VectorStoreError(f"Qdrant 不可达/操作失败（{settings.QDRANT_URL}）: {e}") from e


def upsert_document(doc_id: UUID, kb_id: UUID, texts: list[str], vectors: list[list[float]]) -> int:
    """把文档全部切片写入 Qdrant，返回写入条数。

    payload 含 chunk_id/doc_id/kb_id/tenant_id/idx/text，检索与来源溯源（BU-07）依赖它。
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
    name = settings.QDRANT_COLLECTION
    # point id 用 uuid5 稳定派生（合法 UUID、去重幂等、可溯源），chunk_id 同值
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
    name = settings.QDRANT_COLLECTION
    try:
        client = get_qdrant_client()
        # 先确认集合存在，不存在则无事可删（幂等）
        collections = [c.name for c in client.get_collections().collections]
        if name in collections:
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
