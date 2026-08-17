"""答案缓存单测（T10）：实体锁定 / KB 版本失效 / 开关 / fail-open。mock 掉 Qdrant/Redis。"""
from __future__ import annotations

from app.services import answer_cache
from app.services.answer_cache import COLLECTION, _entities_ok, get, put


class _FakeHit:
    def __init__(self, payload, score=0.99):
        self.payload = payload
        self.score = score


class _FakeQdrant:
    def __init__(self):
        self.collections = {COLLECTION: True}
        self.hits: list[_FakeHit] = []
        self.upserted: list[dict] = []

    def get_collections(self):
        class C:
            collections = [type("c", (), {"name": n})() for n in self.collections]

        return C()

    def create_collection(self, **kw):
        self.collections[COLLECTION] = True

    def search(self, **kw):
        return self.hits

    def upsert(self, **kw):
        self.upserted.append(kw)


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v, ex=None):
        self.store[k] = v

    def delete(self, k):
        self.store.pop(k, None)


def test_entities_lock_blocks_cross_product(monkeypatch):
    """实体锁定：query 含实体但缓存问句缺 → 拒绝命中（防"手机保修"串"冰箱保修"）。"""
    assert _entities_ok("手机保修多久", "手机保修多久")
    assert not _entities_ok("手机保修多久", "冰箱保修多久")
    assert not _entities_ok("Z9 Pro 保修多久", "手机保修多久")
    assert _entities_ok("保修多久", "冰箱保修多久")  # 无实体不限制


def test_get_checks_kb_version(monkeypatch):
    """版本不一致 → miss（陈旧答案不返回）。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    qd = _FakeQdrant()
    qd.hits = [_FakeHit({"question": "保修多久", "answer": "12 个月", "sources": [], "kb_version": "v1"})]
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: qd)
    monkeypatch.setattr(answer_cache, "get_redis", _FakeRedis)
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type("E", (), {"dim": 768, "embed": lambda *a: [[0.1] * 768]})())

    assert get("保修多久", "v1") is not None  # 版本一致命中
    assert get("保修多久", "v2") is None  # 版本过期 miss


def test_get_disabled_returns_none(monkeypatch):
    """开关关闭 → 直接 miss（fail-open 且零开销）。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": False,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    assert get("保修多久", "v1") is None


def test_put_and_get_roundtrip(monkeypatch):
    """回填后精确层可命中（Redis 快路径）。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    qd = _FakeQdrant()
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: qd)
    r = _FakeRedis()
    monkeypatch.setattr(answer_cache, "get_redis", lambda: r)
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type("E", (), {"dim": 768, "embed": lambda *a: [[0.1] * 768]})())

    put("保修多久", "12 个月", [{"chunk_id": "c1"}], ["d1"], "v1")
    assert qd.upserted, "应写入 Qdrant payload（主写）"
    payload = get("保修多久", "v1")
    assert payload and payload["answer"] == "12 个月"
    assert payload["doc_ids"] == ["d1"]

def test_kb_isolation_prevents_cross_kb_hit(monkeypatch):
    """跨 KB 隔离（审查修复）：kb_id 不同的缓存不得互相命中（精确层 key 维度隔离）。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    qd = _FakeQdrant()
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: qd)
    r = _FakeRedis()
    monkeypatch.setattr(answer_cache, "get_redis", lambda: r)
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type("E", (), {"dim": 768, "embed": lambda *a: [[0.1] * 768]})())

    put("保修多久", "KB-A 答案", [{"chunk_id": "c1"}], ["d1"], "v1", kb_id="kb-a")
    # 同 KB 命中（精确层）
    assert get("保修多久", "v1", kb_id="kb-a")["answer"] == "KB-A 答案"
    # 跨 KB 不命中（key 含 kb_id，天然 miss）
    assert get("保修多久", "v1", kb_id="kb-b") is None


def test_kb_mismatch_payload_rejected(monkeypatch):
    """语义层命中但 payload.kb_id 与请求不符 → miss（防跨 KB 串答兜底）。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    qd = _FakeQdrant()
    qd.hits = [_FakeHit({"question": "保修多久", "answer": "KB-B 答案", "sources": [],
                         "kb_version": "v1", "kb_id": "kb-b"})]
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: qd)
    monkeypatch.setattr(answer_cache, "get_redis", _FakeRedis)
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type("E", (), {"dim": 768, "embed": lambda *a: [[0.1] * 768]})())

    assert get("保修多久", "v1", kb_id="kb-a") is None  # payload kb_id=kb-b ≠ 请求 kb-a → miss


def test_semantic_hit_similar_question(monkeypatch):
    """语义层命中（阈值修复）：同义改写问句（相似度 0.94）应命中——0.95 阈值曾导致形同虚设。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.85,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    qd = _FakeQdrant()
    # 语义命中：相似问句返回相似度 0.90（≥0.85 应命中）
    qd.hits = [_FakeHit({"question": "七天无理由退货怎么申请？", "answer": "7天内可退",
                         "sources": [], "kb_version": "v1", "kb_id": "kb-a"})]
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: qd)
    monkeypatch.setattr(answer_cache, "get_redis", _FakeRedis)
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type("E", (), {"dim": 768, "embed": lambda *a: [[0.1] * 768]})())

    r = get("如何申请七天无理由退货？", "v1", kb_id="kb-a")
    assert r is not None and r["answer"] == "7天内可退"
