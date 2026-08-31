"""答案缓存单测（T10）：实体锁定 / KB 版本失效 / 开关 / fail-open。mock 掉 Qdrant/Redis。"""
from __future__ import annotations

from app.services import answer_cache
from app.services.answer_cache import COLLECTION, _entities_ok, _polarity_conflict, get, put


class _FakeHit:
    def __init__(self, payload, score=0.99):
        self.payload = payload
        self.score = score


class _FakeQdrant:
    def __init__(self):
        self.collections = {COLLECTION: True}
        self.hits: list[_FakeHit] = []
        self.upserted: list[dict] = []
        self.fail = False  # m2：模拟 Qdrant 侧失败（集合被外部删除 404 等）

    def get_collections(self):
        class C:
            collections = [type("c", (), {"name": n})() for n in self.collections]

        return C()

    def create_collection(self, **kw):
        self.collections[COLLECTION] = True

    def search(self, **kw):
        if self.fail:
            raise RuntimeError("404: collection answer_cache not found")
        return self.hits

    def upsert(self, **kw):
        if self.fail:
            raise RuntimeError("404: collection answer_cache not found")
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


def test_put_same_query_reuses_deterministic_point_id(monkeypatch):
    """M5（外部审查 2026-08-22）：同一 问题+kb 重复回填必须命中同一 point id。

    确定性 id（uuid5）→ Qdrant upsert 幂等覆盖；旧实现 uuid4 随机 id 语义层只增
    不减，高频问句反复回填导致集合无界膨胀。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    qd = _FakeQdrant()
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: qd)
    monkeypatch.setattr(answer_cache, "get_redis", _FakeRedis)
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type("E", (), {"dim": 768, "embed": lambda *a: [[0.1] * 768]})())

    put("保修多久", "12 个月", [], [], "v1", kb_id="kb-a")
    put("保修多久", "12 个月（更新）", [], [], "v1", kb_id="kb-a")
    put("退货怎么退", "7 天无理由", [], [], "v1", kb_id="kb-a")
    ids = [kw["points"][0]["id"] for kw in qd.upserted]
    assert len(ids) == 3
    assert ids[0] == ids[1], "同问句同库重复回填必须复用同一点（幂等覆盖）"
    assert ids[2] != ids[0], "不同问句必须是不同的点"

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


# --- P2-⑨ -------------------------------------------------------------------


def test_evict_stale_kb_deletes_old_version_points(monkeypatch):
    """P2-⑨：KB 版本推进 → 按过滤条件删除该 KB 旧版本点（must_not=当前版本，单 RPC）。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    monkeypatch.setattr(answer_cache, "_ensured", True)  # 跳过集合 RPC（单进程内已建一次）

    deletes: list[dict] = []

    class _DeleteQdrant:
        def delete(self, **kw):
            deletes.append(kw)

    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: _DeleteQdrant())

    answer_cache.evict_stale_kb("kb-a", "v2")

    assert len(deletes) == 1, f"应恰好一次删除 RPC，实际 {len(deletes)}"
    selector = deletes[0]["points_selector"]
    # FilterSelector 模型：filter.must / filter.must_not 里的 FieldCondition
    must_conds = [c.key for c in selector.filter.must]
    must_not_conds = [c.key for c in selector.filter.must_not]
    assert "kb_id" in must_conds, f"must 应含 kb_id 隔离条件: {must_conds}"
    kb_cond = next(c for c in selector.filter.must if c.key == "kb_id")
    assert kb_cond.match.value == "kb-a"  # 只删目标 KB
    assert "kb_version" in must_not_conds, f"must_not 应排除当前版本: {must_not_conds}"
    ver_cond = next(c for c in selector.filter.must_not if c.key == "kb_version")
    assert ver_cond.match.value == "v2", "必须保留当前版本的点"


def test_evict_stale_kb_disabled_noop(monkeypatch):
    """P2-⑨：开关关闭 → 直接返回，不触碰 Qdrant（零开销）。"""

    def _should_not_touch():
        raise AssertionError("开关关闭时不应触碰 Qdrant")

    monkeypatch.setattr(answer_cache, "settings", type("S", (), {"ANSWER_CACHE_ENABLED": False})())
    monkeypatch.setattr(answer_cache, "get_qdrant_client", _should_not_touch)

    answer_cache.evict_stale_kb("kb-a", "v2")  # 不抛异常即通过


# --- 极性防护（架构审核债 5-1）------------------------------------------------


def test_polarity_conflict_detection():
    """极性判定：query 与缓存问句的极性词集合不一致即冲突；一致（含双方皆无）不冲突。"""
    # 否定翻转：能退 vs 不能退
    assert _polarity_conflict("商品能退货吗", "商品不能退货吗")
    # 条件翻转：7天内 vs 超过7天（单侧含条件词即冲突）
    assert _polarity_conflict("7天内能退货吗", "超过7天能退货吗")
    assert _polarity_conflict("保修多久", "超过7天还能保修吗")
    # 裸"不"兜底：双字否定词覆盖不到的"保修/不保修"类翻转也要拦
    assert _polarity_conflict("手机保修吗", "手机不保修吗")
    # 极性一致（含双方同含否定词）不冲突
    assert not _polarity_conflict("商品能退货吗", "商品可以退货吗")
    assert not _polarity_conflict("不能退货吗", "确实不能退货吗")


def test_semantic_hit_blocked_on_polarity_conflict(monkeypatch):
    """否定/条件翻转防护（架构审核债 5-1）："能退"与"不能退"仅一词之差，假 embed 同向量
    下余弦必然 ≥0.85，但极性相反——不得互相命中；极性一致的改写"可以退吗"仍命中。"""
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.85,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    qd = _FakeQdrant()
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: qd)
    monkeypatch.setattr(answer_cache, "get_redis", _FakeRedis)
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type("E", (), {"dim": 768, "embed": lambda *a: [[0.1] * 768]})())

    put("商品能退货吗", "7 天内可退", [{"chunk_id": "c1"}], ["d1"], "v1", kb_id="kb-a")
    assert qd.upserted, "语义点应已写入"
    # 假 embed 恒同向量 → 以下翻转问句语义检索必然以 0.93 相似度命中该点
    qd.hits = [_FakeHit(qd.upserted[0]["points"][0]["payload"], score=0.93)]
    assert get("商品不能退货吗", "v1", kb_id="kb-a") is None, "否定翻转不得命中缓存"
    assert get("超过7天能退货吗", "v1", kb_id="kb-a") is None, "条件翻转不得命中缓存"
    # 对照：极性一致的改写问句照常语义命中（防护只拦翻转，不伤正常问答）
    hit = get("商品可以退货吗", "v1", kb_id="kb-a")
    assert hit is not None and hit["answer"] == "7 天内可退"


def test_polarity_synonym_families_hit():
    """H3 债清偿：同义否定家族（无法/不可 vs 不能）归一规范类后应互相命中——
    修前裸"不"⊂"不能"致类集合永不等，高频否定问句缓存命中率结构性为 0。"""
    from app.services.answer_cache import _polarity_conflict

    assert not _polarity_conflict("无法退货吗", "为什么不能退货")   # 跨家族归一命中
    assert not _polarity_conflict("可不可以退", "能不能退")          # 归一并极大类后命中
    # 既有断言不翻转：真翻转仍拦
    assert _polarity_conflict("商品能退货吗", "商品不能退货吗")
    # 极大类归并：同侧"不"被"不能"包含不计，两侧类集合相等
    assert not _polarity_conflict("不能退货", "确实不能退")
    # 条件词（超过/之外）与否定词不同类，仍拦
    assert _polarity_conflict("超过7天能退吗", "不能退货")


def test_polarity_negative_prefixes_mei_wei_wu():
    """M5（bughunt-concurrency）：极性词表补「没/未/无/别/莫」——高频口语否定翻转必须拦。

    旧词表缺「没/未/无」："没发货可退" vs "发货了可退" 两问极性类均为空集 →
    相等 → 语义缓存串答（B 拿到 A 的反向答案），实体锁定在无数体句时恒放行。
    """
    from app.services.answer_cache import _polarity_conflict

    assert _polarity_conflict("没发货可以退款吗", "发货了可以退款吗")
    assert _polarity_conflict("未激活可以换货吗", "已激活可以换货吗")
    assert _polarity_conflict("无货什么时候补", "有货吗")
    assert _polarity_conflict("别拆封保修吗", "保修吗")  # 别：单侧否定
    # 同极性仍互相命中（词表从宽：不误拦同向问句）
    assert not _polarity_conflict("没发货可以退款吗", "没发货怎么退款")
    assert not _polarity_conflict("未激活可以换货吗", "未激活怎么换货")


def test_polarity_bukeyi_not_dead_entry():
    """m6（bughunt-concurrency）：canon 死条目「不可以」修复——入词表后被真实采集。

    旧态：「不可以」不在 _POLARITY_TERMS，canon 映射不可达（行为靠「不」+「不可」
    两个子串巧合收敛）；后人按 canon 表扩词会误以为已覆盖。入表后显式生效。
    """
    from app.services.answer_cache import _POLARITY_TERMS, _polarity_conflict

    assert "不可以" in _POLARITY_TERMS
    assert not _polarity_conflict("不可以退货吗", "不能退货吗")


def test_qdrant_failure_resets_ensured(monkeypatch):
    """m2（bughunt-concurrency）：Qdrant 失败复位 _ensured——集合被外部删除后自愈重建。

    旧态：_ensured 进程内恒真，ops 误删集合后 get/put 的 Qdrant 404 走 fail-open
    只打日志 → 缓存命中率归零且重启前无自愈。修复：Qdrant 异常时复位 _ensured，
    下次 get/put 重新 _ensure_collection 建集合。
    """
    monkeypatch.setattr(answer_cache, "settings", type("S", (), {
        "ANSWER_CACHE_ENABLED": True,
        "ANSWER_CACHE_THRESHOLD": 0.95,
        "ANSWER_CACHE_TTL_HOURS": 24,
    })())
    qd = _FakeQdrant()
    qd.fail = True
    monkeypatch.setattr(answer_cache, "get_qdrant_client", lambda: qd)
    monkeypatch.setattr(answer_cache, "get_redis", _FakeRedis)
    monkeypatch.setattr(answer_cache, "get_embedding_client", lambda: type("E", (), {"dim": 768, "embed": lambda *a: [[0.1] * 768]})())
    monkeypatch.setattr(answer_cache, "_ensured", True)  # 模拟早已 ensure 过

    assert get("保修多久", "v1") is None  # fail-open 降级
    assert answer_cache._ensured is False, "Qdrant 失败后应复位 _ensured（下次重建自愈）"

    # 修复后：fail 解除 → 下次 get 重新 ensure 并正常命中
    monkeypatch.setattr(answer_cache, "_ensured", False)
    qd.fail = False
    qd.hits = [_FakeHit({"question": "保修多久", "answer": "12 个月", "sources": [], "kb_version": "v1"})]
    assert get("保修多久", "v1") is not None
    assert answer_cache._ensured is True
