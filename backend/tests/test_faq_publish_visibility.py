"""FAQ 匿名端点 × 发布批次可见性测试（2026-09-27 高危越权补漏，审计 M5 任务 1）。

漏洞事实链：staged 导入（visible=False 只写 Qdrant payload）在 PG 侧同样
mark_indexed —— Document.status=indexed 但从未发布的草案，旧实现只看
status==indexed 即对匿名端点放行原文（raw_text 全网可读），且 doc_id 可从
列表端点枚举。修复口径（Orchestrator 裁定，以 KBPublishBatch 为 PG 真源推导）：
① 无批次（直通导入）→ 可见；②③④ 有批次 → 仅「所属最新批次 released」可见
（staged/评测中/失败/回滚不可见）；⑤ 回滚后重新发布进新批次 → 重新可见，
且旧批次 released + 新批次未发布 → 必须重新隐藏（专杀「任一批次 released
即可见」的错误规则——Qdrant payload 后翻转覆盖，最新批次才反映现势）。

测试直接落 PG 行（Document + KBPublishBatch），不依赖 Qdrant/真实 LLM；
批次 doc_ids 按生产写法存 **str(uuid)**（upsert_batch_membership 同款），
与 Document.id（UUID）跨类型匹配是显式覆盖点（含大写/非法条目规范化）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import app.models.knowledge  # noqa: F401  # 注册表元数据
import pytest
from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.kb_publish import KBBatchStatus, KBPublishBatch
from app.models.knowledge import Chunk, Document, DocumentStatus, KnowledgeBase
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

KB_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")
DOC_FREE_ID = uuid.UUID("70000000-0000-0000-0000-000000000001")      # ① 无批次
DOC_STAGED_ID = uuid.UUID("70000000-0000-0000-0000-000000000002")    # ② pending 批次
DOC_RELEASED_ID = uuid.UUID("70000000-0000-0000-0000-000000000003")  # ③ released 批次
DOC_ROLLED_ID = uuid.UUID("70000000-0000-0000-0000-000000000004")    # ④ rolled_back 批次
DOC_REPUB_ID = uuid.UUID("70000000-0000-0000-0000-000000000005")     # ⑤ 多批次生命周期
DOC_TYPE_ID = uuid.UUID("70000000-0000-0000-0000-000000000006")      # 类型规范化

_T0 = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)


@pytest.fixture
def env():
    """独立 SQLite：KB + 6 篇 indexed 文档 + 各场景批次行（doc_ids 存 str(uuid)）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[
            KnowledgeBase.__table__,
            Document.__table__,
            Chunk.__table__,
            KBPublishBatch.__table__,
        ],
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    with Local() as db:
        db.add(KnowledgeBase(id=KB_ID, name="公开政策库", description="d"))
        for doc_id in (
            DOC_FREE_ID, DOC_STAGED_ID, DOC_RELEASED_ID,
            DOC_ROLLED_ID, DOC_REPUB_ID, DOC_TYPE_ID,
        ):
            db.add(
                Document(
                    id=doc_id,
                    kb_id=KB_ID,
                    name=f"{doc_id}.md",
                    status=DocumentStatus.indexed,
                    chunk_count=1,
                    sha256=str(doc_id) * 2,
                    raw_text=f"草案原文 {doc_id}——未发布不得外泄",
                )
            )
        db.commit()
    yield Local
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client(env):
    def _override():
        db = env()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c


def _seed_batch(db, batch_id: str, status: KBBatchStatus, doc_ids: list, mins: int) -> KBPublishBatch:
    """落批次行：created_at 显式递增（SQLite server_default 秒级精度，靠它排序会并刻）。"""
    ts = _T0 + timedelta(minutes=mins)
    b = KBPublishBatch(
        kb_id=KB_ID,
        batch_id=batch_id,
        status=status,
        doc_ids=doc_ids,
        created_at=ts,
        updated_at=ts,
    )
    db.add(b)
    db.commit()
    return b


def _listed_ids(client) -> set[str]:
    r = client.get(f"{API}/faq")
    assert r.status_code == 200
    return {d["doc_id"] for kb in r.json()["items"] for d in kb["docs"]}


# ---------------------------------------------------------------- ① 直通导入


def test_faq_direct_import_no_batch_visible(env, client):
    """① 无批次（直通导入路径）：列表可枚举 + 原文 200。"""
    assert str(DOC_FREE_ID) in _listed_ids(client)
    r = client.get(f"{API}/faq/docs/{DOC_FREE_ID}/content")
    assert r.status_code == 200
    assert "草案原文" in r.json()["content"]


# ---------------------------------------------------------------- ② staged


def test_faq_staged_batch_hidden(env, client):
    """② 批次 pending（staged 未发布）：列表不得枚举其 doc_id + 内容 404。

    这正是修复前的高危窗口：PG status=indexed、Qdrant visible=False、批次未发布
    ——旧实现（只看 indexed）此处 200，全网可读草案原文。
    """
    with env() as db:
        _seed_batch(db, "b-staged", KBBatchStatus.pending, [str(DOC_STAGED_ID)], 10)
    assert str(DOC_STAGED_ID) not in _listed_ids(client)
    r = client.get(f"{API}/faq/docs/{DOC_STAGED_ID}/content")
    assert r.status_code == 404
    assert "文档" in r.json()["message"]  # 业务 404 文案，防路由缺失假绿


def test_faq_evaluating_batch_hidden(env, client):
    """② 补充：evaluating（快检进行中）同样不可见——发布仅认 released。"""
    with env() as db:
        _seed_batch(db, "b-eval", KBBatchStatus.evaluating, [str(DOC_STAGED_ID)], 10)
    assert str(DOC_STAGED_ID) not in _listed_ids(client)
    assert client.get(f"{API}/faq/docs/{DOC_STAGED_ID}/content").status_code == 404


# ---------------------------------------------------------------- ③ released


def test_faq_released_batch_visible(env, client):
    """③ 批次 released：列表可枚举 + 原文 200（修复不得误伤已发布内容）。"""
    with env() as db:
        _seed_batch(db, "b-rel", KBBatchStatus.released, [str(DOC_RELEASED_ID)], 20)
    assert str(DOC_RELEASED_ID) in _listed_ids(client)
    r = client.get(f"{API}/faq/docs/{DOC_RELEASED_ID}/content")
    assert r.status_code == 200
    assert r.json()["status"] == "indexed"


# ---------------------------------------------------------------- ④ rolled_back


def test_faq_rolled_back_batch_hidden(env, client):
    """④ 批次 rolled_back：回滚后原文撤回——列表不可枚举 + 内容 404。"""
    with env() as db:
        _seed_batch(db, "b-rb", KBBatchStatus.rolled_back, [str(DOC_ROLLED_ID)], 30)
    assert str(DOC_ROLLED_ID) not in _listed_ids(client)
    r = client.get(f"{API}/faq/docs/{DOC_ROLLED_ID}/content")
    assert r.status_code == 404
    assert "文档" in r.json()["message"]


# ---------------------------------------------------------------- ⑤ 回滚后重发布（多批次生命周期）


def test_faq_rollback_then_republish_new_batch(env, client):
    """⑤ 同一 doc 的多批次生命周期，按「最新批次」判定（不是任一批次）。

    - 步 1：旧批次 rolled_back + 新批次 released → 200（回滚后重新发布应重新可见）；
    - 步 2：再挂一个更新的 pending 批次（内容更新 staged 中）→ 必须重新 404/隐藏。
      「任一批次 released 即可见」的错误规则在步 2 会误判可见（旧 released 仍算数）
      ——本断言专杀该规则；Qdrant 侧真实行为是重导入以 visible=False 覆盖 payload，
      最新批次赢才与检索侧一致。
    - 步 3：新批次发布（released）→ 200（翻转即时生效）。
    """
    with env() as db:
        _seed_batch(db, "b-old-rb", KBBatchStatus.rolled_back, [str(DOC_REPUB_ID)], 40)
        _seed_batch(db, "b-new-rel", KBBatchStatus.released, [str(DOC_REPUB_ID)], 50)
    assert client.get(f"{API}/faq/docs/{DOC_REPUB_ID}/content").status_code == 200
    assert str(DOC_REPUB_ID) in _listed_ids(client)

    with env() as db:
        _seed_batch(db, "b-stg2", KBBatchStatus.pending, [str(DOC_REPUB_ID)], 60)
    assert client.get(f"{API}/faq/docs/{DOC_REPUB_ID}/content").status_code == 404
    assert str(DOC_REPUB_ID) not in _listed_ids(client)

    with env() as db:
        b = db.query(KBPublishBatch).filter_by(batch_id="b-stg2").one()
        b.status = KBBatchStatus.released
        db.commit()
    assert client.get(f"{API}/faq/docs/{DOC_REPUB_ID}/content").status_code == 200
    assert str(DOC_REPUB_ID) in _listed_ids(client)


# ---------------------------------------------------------------- 类型/格式坑


def test_faq_batch_doc_ids_string_type_matched(env, client):
    """doc_ids 存的是 **uuid 字符串**（生产写法 str(doc_id)），Document.id 是 UUID。

    批次含两条目：大写变体（跨类型比对若不做规范化就比不中 → doc 被误判
    「无批次可见」）+ 非法串（不得让端点 500）。pending 批次 → 期望不可见：
    规范化缺失时大写条目匹配不上 → 误可见 → 本测试红。
    """
    with env() as db:
        _seed_batch(
            db,
            "b-upper",
            KBBatchStatus.pending,
            [str(DOC_TYPE_ID).upper(), "not-a-uuid", "  "],
            70,
        )
    assert client.get(f"{API}/faq/docs/{DOC_TYPE_ID}/content").status_code == 404
    assert str(DOC_TYPE_ID) not in _listed_ids(client)
