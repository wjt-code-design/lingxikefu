"""vector_service.ensure_collection 幂等/缓存回归（Bug A，2026-08-27 修复验证）。

背景：ensure_collection 创建集合并成功返回后，必须刷新 ``_COLLECTIONS_CACHE``；
否则 60s TTL 内紧接的"以为集合还不存在"的 ensure 会重复 PUT create → Qdrant 409，
eval 导入 12 文档 FAIL-IMPORT（CI 实测实证）。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.core.config import settings
from app.services import vector_service


def test_ensure_collection_creates_once_and_refreshes_cache(monkeypatch):
    """创建集合后缓存必须刷新：紧接的第二次 ensure 不得重复 create（防 409）。"""
    monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", False)  # 纯 dense：维度校验走 .size

    class FakeQdrant:
        def __init__(self) -> None:
            self.collections: list[str] = []
            self.created = 0

        def get_collections(self):
            return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self.collections])

        def create_collection(self, collection_name: str, **kw) -> None:
            self.collections.append(collection_name)
            self.created += 1

        def get_collection(self, name: str):
            return SimpleNamespace(
                config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=768)))
            )

    fake = FakeQdrant()
    monkeypatch.setattr("app.services.vector_service.get_qdrant_client", lambda: fake)
    monkeypatch.setattr(
        "app.services.vector_service.get_embedding_client", lambda: SimpleNamespace(dim=768)
    )
    try:
        vector_service._COLLECTIONS_CACHE = None  # 清缓存，防测试间串扰

        assert vector_service.ensure_collection() == 768
        # 创建成功后必须置 None（缓存刷新），否则第二次 ensure 仍用旧列表、误判"不存在"
        assert vector_service._COLLECTIONS_CACHE is None
        assert vector_service.ensure_collection() == 768
        assert fake.created == 1  # 只创建一次：第二次 ensure 走"已存在"分支（不 PUT create）
    finally:
        vector_service._COLLECTIONS_CACHE = None
