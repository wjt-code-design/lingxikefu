"""vector_service 测试：ensure_collection 幂等/缓存回归（Bug A）+ 写入/删除路径参数构造。

- Bug A（2026-08-27 修复）：ensure_collection 创建集合并成功返回后，必须刷新
  ``_COLLECTIONS_CACHE``；否则 60s TTL 内紧接的"以为集合还不存在"的 ensure 会重复
  PUT create → Qdrant 409，eval 导入 12 文档 FAIL-IMPORT（CI 实测实证）。
- 写入/删除（盲区补齐）：upsert_document（hybrid named vectors / 纯 dense、payload、
  维度与数量校验、错误包装）与 delete_by_doc_id（不存在的集合幂等、doc_id 过滤构造）。
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.core.config import settings
from app.services import vector_service
from app.services.vector_service import VectorStoreError


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


# ---------------------------------------------------------------------------
# 写入/删除路径（盲区补齐：upsert_document / delete_by_doc_id 参数构造）
# ---------------------------------------------------------------------------


class _FakeQdrant:
    """可配置的 Qdrant fake：记录 create/get/upsert/delete 调用，供参数构造断言。"""

    def __init__(self, *, hybrid: bool = False, dim: int = 768) -> None:
        self.hybrid = hybrid
        self.dim = dim
        self.collections: list[str] = []
        self.created = 0
        self.upsert_calls: list[tuple[str, list]] = []
        self.delete_calls: list[tuple[str, object]] = []
        self.fail_upsert: Exception | None = None
        self.fail_delete: Exception | None = None

    def get_collections(self):
        return SimpleNamespace(collections=[SimpleNamespace(name=n) for n in self.collections])

    def create_collection(self, collection_name: str, **kw) -> None:
        self.collections.append(collection_name)
        self.created += 1

    def get_collection(self, name: str):
        # hybrid → named vectors 结构；纯 dense → 带 .size 的对象（get_collection 真实返回形态）
        vectors = (
            {"dense": SimpleNamespace(size=self.dim)}
            if self.hybrid
            else SimpleNamespace(size=self.dim)
        )
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=vectors))
        )

    def upsert(self, collection_name: str, points: list) -> object:
        if self.fail_upsert:
            raise self.fail_upsert
        self.upsert_calls.append((collection_name, points))
        return SimpleNamespace(status="completed")

    def delete(self, collection_name: str, points_selector: object) -> None:
        if self.fail_delete:
            raise self.fail_delete
        self.delete_calls.append((collection_name, points_selector))


@pytest.fixture(autouse=True)
def _reset_collections_cache():
    """每测清空集合列表缓存（60s TTL 的模块级变量），防测试间串扰。"""
    vector_service._COLLECTIONS_CACHE = None
    yield
    vector_service._COLLECTIONS_CACHE = None


@pytest.fixture
def fake_qdrant(monkeypatch):
    def _make(*, hybrid: bool = False):
        fake = _FakeQdrant(hybrid=hybrid)
        monkeypatch.setattr("app.services.vector_service.get_qdrant_client", lambda: fake)
        monkeypatch.setattr(
            "app.services.vector_service.get_embedding_client",
            lambda: SimpleNamespace(dim=fake.dim),
        )
        return fake

    return _make


class TestCollectionNameAndPointId:
    def test_get_collection_name_hybrid_vs_dense(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", True)
        assert vector_service.get_collection_name() == settings.QDRANT_COLLECTION_HYBRID
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", False)
        assert vector_service.get_collection_name() == settings.QDRANT_COLLECTION

    def test_point_id_deterministic_and_unique_per_idx(self):
        doc_id = uuid4()
        assert vector_service._point_id(doc_id, 1) == vector_service._point_id(doc_id, 1)
        assert vector_service._point_id(doc_id, 1) != vector_service._point_id(doc_id, 2)
        UUID(vector_service._point_id(doc_id, 0))  # 必须是合法 UUID（Qdrant 只接受纯 UUID）

    def test_point_id_differs_across_docs(self):
        assert vector_service._point_id(uuid4(), 0) != vector_service._point_id(uuid4(), 0)


class TestUpsertDocument:
    def test_mismatched_lengths_raises(self, fake_qdrant):
        fake_qdrant()
        with pytest.raises(VectorStoreError, match="数量不一致"):
            vector_service.upsert_document(uuid4(), uuid4(), ["a", "b"], [[0.1, 0.2]])

    def test_wrong_vector_dim_raises(self, fake_qdrant, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", False)
        fake_qdrant()
        with pytest.raises(VectorStoreError, match="向量维度"):
            vector_service.upsert_document(
                uuid4(), uuid4(), ["a"], [[0.1, 0.2]]  # 2 维 != 768 维
            )

    def test_hybrid_named_vectors_payload(self, fake_qdrant, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", True)
        fake = fake_qdrant(hybrid=True)
        doc_id, kb_id = uuid4(), uuid4()
        texts = ["你好世界", "退货规则支持七天无理由"]
        vectors = [[0.1] * 768, [0.2] * 768]

        n = vector_service.upsert_document(doc_id, kb_id, texts, vectors)

        assert n == 2
        coll, points = fake.upsert_calls[0]
        assert coll == settings.QDRANT_COLLECTION_HYBRID
        from qdrant_client.http.models import SparseVector

        for idx, point in enumerate(points):
            assert point["id"] == vector_service._point_id(doc_id, idx)
            assert point["payload"]["doc_id"] == str(doc_id)
            assert point["payload"]["kb_id"] == str(kb_id)
            assert point["payload"]["tenant_id"] == settings.TENANT_DEFAULT
            assert point["payload"]["idx"] == idx
            assert point["payload"]["text"] == texts[idx]
            assert point["payload"]["chunk_id"] == point["id"]
            # hybrid：named vectors → dense + sparse 双向量
            assert set(point["vector"]) == {"dense", "sparse"}
            assert isinstance(point["vector"]["sparse"], SparseVector)
            assert len(point["vector"]["dense"]) == 768

    def test_dense_plain_vectors_payload(self, fake_qdrant, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", False)
        fake = fake_qdrant()
        doc_id, kb_id = uuid4(), uuid4()
        texts = ["纯向量检索测试"]

        vector_service.upsert_document(doc_id, kb_id, texts, [[0.5] * 768])

        coll, points = fake.upsert_calls[0]
        assert coll == settings.QDRANT_COLLECTION
        # 纯 dense：vector 是普通 list，不带 sparse
        assert points[0]["vector"] == [0.5] * 768
        assert points[0]["payload"]["text"] == texts[0]
        assert points[0]["payload"]["chunk_id"] == points[0]["id"]

    def test_upsert_error_wrapped(self, fake_qdrant, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", False)
        fake = fake_qdrant()
        fake.fail_upsert = RuntimeError("connection lost")
        with pytest.raises(VectorStoreError, match="upsert 失败"):
            vector_service.upsert_document(uuid4(), uuid4(), ["a"], [[0.1] * 768])


class TestDeleteByDocId:
    def test_collection_missing_is_idempotent(self, fake_qdrant):
        fake = fake_qdrant()  # collections 为空 → 集合不存在
        vector_service.delete_by_doc_id(uuid4())
        assert fake.delete_calls == []  # 无事可删，静默返回

    def test_builds_filter_on_doc_id(self, fake_qdrant, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", False)
        fake = fake_qdrant()
        fake.collections = [settings.QDRANT_COLLECTION]
        doc_id = uuid4()

        vector_service.delete_by_doc_id(doc_id)

        assert len(fake.delete_calls) == 1
        coll, selector = fake.delete_calls[0]
        assert coll == settings.QDRANT_COLLECTION
        from qdrant_client.http.models import FilterSelector

        assert isinstance(selector, FilterSelector)
        must = selector.filter.must
        assert len(must) == 1
        assert must[0].key == "doc_id"
        assert must[0].match.value == str(doc_id)  # 精确匹配该文档（不误删其他文档）

    def test_uses_hybrid_collection_name(self, fake_qdrant, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", True)
        fake = fake_qdrant()
        fake.collections = [settings.QDRANT_COLLECTION_HYBRID]
        vector_service.delete_by_doc_id(uuid4())
        assert fake.delete_calls[0][0] == settings.QDRANT_COLLECTION_HYBRID

    def test_delete_error_wrapped(self, fake_qdrant, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", False)
        fake = fake_qdrant()
        fake.collections = [settings.QDRANT_COLLECTION]
        fake.fail_delete = RuntimeError("qdrant down")
        with pytest.raises(VectorStoreError, match="删除文档向量失败"):
            vector_service.delete_by_doc_id(uuid4())


class TestEnsureDimensionGuard:
    def test_dim_mismatch_raises(self, fake_qdrant, monkeypatch):
        """集合维度 != embedding 维度 → 拒绝（防换 provider 后误写旧集合）。"""
        monkeypatch.setattr(settings, "RAG_ENABLE_HYBRID", False)
        fake = fake_qdrant()
        fake.collections = [settings.QDRANT_COLLECTION]  # 已存在，dim=768
        monkeypatch.setattr(
            "app.services.vector_service.get_embedding_client",
            lambda: SimpleNamespace(dim=1024),  # embedding 换了维度
        )
        with pytest.raises(VectorStoreError, match="维度"):
            vector_service.ensure_collection()
