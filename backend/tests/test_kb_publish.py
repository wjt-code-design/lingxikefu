"""KB 发布流编排测试（门禁 v2 G2）：batch 状态机 + 上传 staged 通道 + 快检触发 + 发布/回滚翻转。

- SQLite StaticPool（同 test_knowledge_api 约定），建 knowledge + kb_publish_batches +
  eval_results + notifications + audit_logs 表；
- 快检桩：monkeypatch scripts.eval_faithfulness.run_faithfulness_eval（test_eval_api 同手法），
  后台 job 经独立会话（SessionLocal 替换为测试引擎）落表后翻转批次状态，
  测试轮询列表端点等待终态（TestClient portal 循环持续跑 create_task，同评测中心端到端先例）；
- 状态机锁定：pending/failed/rolled_back → publish 202 → evaluating → released/failed；
  evaluating/released 重复 publish 409；仅 released 可 rollback（否则 409）。
"""
from __future__ import annotations

import asyncio
import time
import uuid as uuid_mod
from uuid import uuid4

import app.models.kb_publish  # noqa: F401  注册表到 Base.metadata
import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.eval_result import EvalResult, EvalStatus
from app.models.kb_publish import KBBatchStatus, KBPublishBatch
from app.models.knowledge import Chunk, Document, DocumentStatus, KnowledgeBase
from app.models.notification import Notification
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
BATCH_API = f"{API}/admin/kb/batches"


def _headers(role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token('u1', role)}"}


def test_upsert_membership_no_lost_update(monkeypatch, env):
    """M2（bughunt-concurrency）：并发上传同批次——行锁重读防陈旧快照覆盖丢 doc_id。

    旧实现读 batch.doc_ids → 内存追加 → commit（读-改-写无锁）：并发上传时
    A/B 都读到 [d1] → B 提交 [d1,d2]、A 提交 [d1,d3] → d2 从清单丢失 →
    发布翻转按清单走，d2 永远 staged 检索不可见。修复：追加前
    refresh(with_for_update) 行锁重读最新清单。
    """
    from app.services import kb_publish_service as svc

    c, Local = env
    d1, d2, d3 = str(uuid4()), str(uuid4()), str(uuid4())
    kb, batch, _docs = _seed_batch(Local())
    batch_id = batch.batch_id
    # 收敛到纯净场景：清单初始仅 d1
    with Local() as db:
        row = db.query(KBPublishBatch).filter_by(batch_id=batch_id).first()
        row.doc_ids = [d1]
        db.commit()

    real_get = svc.get_batch
    fired = {"once": False}

    def fake_get(db, bid):
        got = real_get(db, bid)
        if got is not None and not fired["once"]:
            fired["once"] = True
            # 模拟并发请求 B：A 首查之后追加 d2 并提交（独立会话写库）
            with Local() as db2:
                row2 = db2.query(KBPublishBatch).filter_by(batch_id=bid).first()
                row2.doc_ids = [*row2.doc_ids, d2]
                db2.commit()
        return got

    monkeypatch.setattr(svc, "get_batch", fake_get)
    with Local() as db:
        svc.upsert_batch_membership(db, kb.id, batch_id, uuid_mod.UUID(d3))
    with Local() as db:
        row = db.query(KBPublishBatch).filter_by(batch_id=batch_id).first()
        got = {str(x) for x in row.doc_ids}
    assert d2 in got, f"并发请求 B 追加的 d2 被陈旧快照覆盖丢失：{got}"
    assert d3 in got, f"本次追加的 d3 丢失：{got}"


def test_recover_orphan_evaluating_batches(env):
    """M4（bughunt-concurrency）：启动对账——超时 evaluating 孤儿批次标 failed + admin 通知。

    快检 ~20min 窗口内服务重启/崩溃/兜底失败 → 批次永久卡 evaluating
    （publish 409、上传 400、列表永远「评测中」）。启动对账把超过阈值的
    evaluating 批次标 failed 并通知，恢复可重发布。
    """
    from datetime import UTC, datetime, timedelta

    import sqlalchemy as sa
    from app.services.kb_publish_service import recover_orphan_evaluating_batches

    c, Local = env
    _kb, fresh, _docs = _seed_batch(Local(), status=KBBatchStatus.evaluating)
    _kb2, orphan, _docs2 = _seed_batch(Local(), status=KBBatchStatus.evaluating)
    with Local() as db:
        # 把 orphan 的 updated_at 拨回 2 小时前（绕过 onupdate）；fresh 保持新鲜
        db.execute(
            sa.text("UPDATE kb_publish_batches SET updated_at = :t WHERE batch_id = :b"),
            {"t": datetime.now(UTC) - timedelta(hours=2), "b": orphan.batch_id},
        )
        db.commit()

    with Local() as db:
        n = recover_orphan_evaluating_batches(db, max_age_minutes=40)
    assert n == 1
    with Local() as db:
        assert db.query(KBPublishBatch).filter_by(batch_id=orphan.batch_id).first().status == KBBatchStatus.failed
        assert db.query(KBPublishBatch).filter_by(batch_id=fresh.batch_id).first().status == KBBatchStatus.evaluating
        notes = db.query(Notification).filter_by(resource_id=orphan.batch_id).all()
        assert notes, "孤儿批次恢复未发 admin 通知"


@pytest.fixture
def env(monkeypatch):
    """测试环境：内存库 + get_db 覆盖 + 后台 job 独立会话绑定同一测试引擎。"""
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
            EvalResult.__table__,
            Notification.__table__,
            AuditLog.__table__,
        ],
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    # 后台 job 的独立会话必须打到测试库（真 SessionLocal 会写入本地 PG）
    monkeypatch.setattr("app.core.database.SessionLocal", Local)
    with TestClient(app) as c:
        yield c, Local
    app.dependency_overrides.pop(get_db, None)


def _seed_batch(
    db,
    *,
    status: KBBatchStatus = KBBatchStatus.pending,
    doc_statuses: tuple[DocumentStatus, ...] = (DocumentStatus.indexed,),
    batch_id: str | None = None,
):
    """造一个批次 + 按给定状态造文档（绕过上传路径，专注编排语义）。"""
    kb = KnowledgeBase(name=f"批次库-{uuid_mod.uuid4().hex[:6]}")
    db.add(kb)
    db.commit()
    docs = []
    for i, s in enumerate(doc_statuses):
        d = Document(
            kb_id=kb.id,
            name=f"doc{i}.md",
            sha256=f"sha-{uuid_mod.uuid4().hex}",
            status=s,
            raw_text="内容",
        )
        db.add(d)
        docs.append(d)
    db.commit()
    batch = KBPublishBatch(
        kb_id=kb.id,
        batch_id=batch_id or f"b-{uuid_mod.uuid4().hex[:8]}",
        status=status,
        doc_ids=[str(d.id) for d in docs],
    )
    db.add(batch)
    db.commit()
    return kb, batch, docs


def _stub_eval(monkeypatch, result=None, exc: Exception | None = None) -> dict:
    """快检桩：记录调用参数（sample/kb_id 必须正确），返回预设指标或抛异常。"""
    captured: dict = {}

    async def fake_eval(db, limit=0, kb_name=None, sample=0, kb_id=None):
        captured["sample"] = sample
        captured["kb_id"] = kb_id
        captured["kb_name"] = kb_name
        await asyncio.sleep(0)
        if exc is not None:
            raise exc
        return result if result is not None else [("qa", 0.9, 10, 9), ("refuse", 1.0, 5, 5)]

    monkeypatch.setattr(
        "scripts.eval_faithfulness.run_faithfulness_eval", fake_eval, raising=False
    )
    return captured


def _stub_flip(monkeypatch) -> list:
    flips: list[tuple] = []

    def fake_flip(doc_ids, visible, batch_tag=None):
        flips.append(([str(d) for d in doc_ids], visible, batch_tag))

    monkeypatch.setattr(
        "app.services.vector_service.set_visible_by_doc_ids", fake_flip
    )
    return flips


def _wait_batch(client, batch_id: str, want: str, timeout: float = 8.0) -> dict:
    """轮询列表端点直到批次到指定状态（后台 create_task 在 portal 循环异步执行）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(BATCH_API, headers=_headers())
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        items = {i["batch_id"]: i for i in r.json()["items"]}
        last = items.get(batch_id, {}).get("status")
        if last == want:
            return items[batch_id]
        time.sleep(0.05)
    raise AssertionError(f"批次 {batch_id} 未到 {want}，当前 {last}")


# ---------------------------------------------------------------------------
# 模型 / 迁移
# ---------------------------------------------------------------------------


def test_batch_model_shape_and_tenant_conventions():
    """kb_publish_batches：tenant_id 红线 + 状态枚举五值 + doc_ids/eval_result_id 可空语义。"""
    t = Base.metadata.tables["kb_publish_batches"]
    pk = {c.name for c in t.primary_key.columns}
    first_non_pk = next(c.name for c in t.columns if c.name not in pk)
    assert first_non_pk == "tenant_id", "第一个非主键列必须是 tenant_id（红线⑨）"
    assert {f"ix_{t.name}_tenant_id"} <= {ix.name for ix in t.indexes}
    status_col = t.columns["status"].type
    assert getattr(status_col, "enum_class", None) is KBBatchStatus or status_col.enums == [
        s.value for s in KBBatchStatus
    ], "status 必须绑定五值状态枚举"
    assert t.columns["batch_id"].unique or any(
        ix.unique and "batch_id" in [c.name for c in ix.columns] for ix in t.indexes
    ), "batch_id 必须唯一索引"
    assert t.columns["eval_result_id"].nullable, "eval_result_id 可空（评测前/重发布清空）"


def test_migration_0020_chain():
    """0020 迁移挂在 0019 之后（链完整性锁定）。"""
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0020_kb_publish_batches.py"
    assert p.exists(), "缺 0020 迁移文件"
    text = p.read_text(encoding="utf-8")
    assert 'revision = "0020"' in text
    assert 'down_revision = "0019"' in text


# ---------------------------------------------------------------------------
# 上传 staged 通道（batch_id 可选参数）
# ---------------------------------------------------------------------------


class _FakeTask:
    """模拟 Celery 任务：delay 记录调用参数（不走 broker / 降级线程）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    def delay(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return None


def test_upload_with_batch_id_creates_pending_batch_and_staged_import(env, monkeypatch):
    """带 batch_id 上传：隐式建批次（pending）+ 文档以 visible=False staged 导入。"""
    c, Local = env
    kb_id = c.post(
        f"{API}/knowledge-bases", json={"name": "发布库"}, headers=_headers()
    ).json()["kb_id"]
    fake_task = _FakeTask()
    monkeypatch.setattr("app.workers.import_worker.import_document_task", fake_task)

    r = c.post(
        f"{API}/knowledge-bases/{kb_id}/documents?batch_id=b-001",
        files={"file": ("新政策.md", " staged 内容".encode(), "text/markdown")},
        headers=_headers(),
    )
    assert r.status_code == 201, f"HTTP {r.status_code}: {r.text}"
    doc_id = r.json()["doc_id"]

    # 导入调度必须带 staged 参数（visible=False + batch_tag）
    assert fake_task.calls, "导入任务未被调度"
    args, kwargs = fake_task.calls[0]
    assert args == (doc_id,)
    assert kwargs.get("visible") is False
    assert kwargs.get("batch_tag") == "b-001"

    with Local() as db:
        batch = db.query(KBPublishBatch).filter_by(batch_id="b-001").one()
        assert str(batch.kb_id) == kb_id
        assert batch.status == KBBatchStatus.pending
        assert doc_id in [str(d) for d in batch.doc_ids]


def test_upload_second_doc_appends_to_existing_batch(env, monkeypatch):
    """同 batch_id 第二个文档：追加 doc_ids，不重复建行。"""
    c, Local = env
    kb_id = c.post(
        f"{API}/knowledge-bases", json={"name": "发布库2"}, headers=_headers()
    ).json()["kb_id"]
    monkeypatch.setattr("app.workers.import_worker.import_document_task", _FakeTask())
    for name in ("a.md", "b.md"):
        r = c.post(
            f"{API}/knowledge-bases/{kb_id}/documents?batch_id=b-002",
            files={"file": (name, f"{name} 内容".encode(), "text/markdown")},
            headers=_headers(),
        )
        assert r.status_code == 201
    with Local() as db:
        batches = db.query(KBPublishBatch).filter_by(batch_id="b-002").all()
        assert len(batches) == 1
        assert len(batches[0].doc_ids) == 2


def test_upload_without_batch_id_unchanged(env, monkeypatch):
    """不带 batch_id：现状直通——不建批次、导入不带 staged 参数（零变化锁定）。"""
    c, Local = env
    kb_id = c.post(
        f"{API}/knowledge-bases", json={"name": "普通库"}, headers=_headers()
    ).json()["kb_id"]
    fake_task = _FakeTask()
    monkeypatch.setattr("app.workers.import_worker.import_document_task", fake_task)
    r = c.post(
        f"{API}/knowledge-bases/{kb_id}/documents",
        files={"file": ("x.md", "普通内容".encode(), "text/markdown")},
        headers=_headers(),
    )
    assert r.status_code == 201
    args, kwargs = fake_task.calls[0]
    assert kwargs == {}, "直通路径不得携带 staged 参数"
    with Local() as db:
        assert db.query(KBPublishBatch).count() == 0


def test_upload_batch_dedupe_rejected_400(env, monkeypatch):
    """批次内 sha256 去重命中已有（已发布可见）文档 → 400 拒绝（不留可见/暂存混挂）。"""
    c, Local = env
    kb_id = c.post(
        f"{API}/knowledge-bases", json={"name": "去重库"}, headers=_headers()
    ).json()["kb_id"]
    monkeypatch.setattr("app.workers.import_worker.import_document_task", _FakeTask())
    content = "重复内容".encode()
    r1 = c.post(
        f"{API}/knowledge-bases/{kb_id}/documents?batch_id=b-003",
        files={"file": ("a.md", content, "text/markdown")},
        headers=_headers(),
    )
    assert r1.status_code == 201
    r2 = c.post(
        f"{API}/knowledge-bases/{kb_id}/documents?batch_id=b-003",
        files={"file": ("a2.md", content, "text/markdown")},
        headers=_headers(),
    )
    assert r2.status_code == 400, f"去重应拒绝，实际 {r2.status_code}: {r2.text}"


def test_upload_batch_id_conflicts_other_kb(env, monkeypatch):
    """batch_id 已绑定其他 KB → 400（batch_id 全局唯一索引语义）。"""
    c, Local = env
    monkeypatch.setattr("app.workers.import_worker.import_document_task", _FakeTask())
    with Local() as db:
        kb_other = KnowledgeBase(name="别的库")
        db.add(kb_other)
        db.commit()
        db.add(
            KBPublishBatch(
                kb_id=kb_other.id, batch_id="b-own", status=KBBatchStatus.pending, doc_ids=[]
            )
        )
        db.commit()
    kb_id = c.post(
        f"{API}/knowledge-bases", json={"name": "本库"}, headers=_headers()
    ).json()["kb_id"]
    r = c.post(
        f"{API}/knowledge-bases/{kb_id}/documents?batch_id=b-own",
        files={"file": ("x.md", "内容".encode(), "text/markdown")},
        headers=_headers(),
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# publish：校验 + 202 + 快检 + 翻转
# ---------------------------------------------------------------------------


def test_publish_requires_admin(env):
    c, _ = env
    assert c.post(f"{BATCH_API}/nope/publish", headers=_headers("user")).status_code == 403
    assert c.post(f"{BATCH_API}/nope/publish").status_code == 401


def test_publish_unknown_batch_404(env):
    c, _ = env
    r = c.post(f"{BATCH_API}/ghost/publish", headers=_headers())
    assert r.status_code == 404


@pytest.mark.parametrize(
    "doc_statuses",
    [
        (DocumentStatus.failed,),
        (DocumentStatus.indexed, DocumentStatus.failed),
        (DocumentStatus.parsing,),
        (DocumentStatus.embedding,),
    ],
    ids=["failed", "mixed-failed", "parsing", "embedding"],
)
def test_publish_rejects_unready_docs_400(env, doc_statuses):
    """有 failed → 400 提示先修复；未完成导入（parsing/embedding）→ 400（须全部 indexed）。"""
    c, Local = env
    _, batch, _ = _seed_batch(db=Local(), doc_statuses=doc_statuses)
    r = c.post(f"{BATCH_API}/{batch.batch_id}/publish", headers=_headers())
    assert r.status_code == 400, f"HTTP {r.status_code}: {r.text}"
    with Local() as db:
        refreshed = db.get(KBPublishBatch, batch.id)
        assert refreshed.status == KBBatchStatus.pending, "校验拒绝不得改状态"


def test_publish_empty_batch_400(env):
    c, Local = env
    _, batch, _ = _seed_batch(db=Local(), doc_statuses=())
    r = c.post(f"{BATCH_API}/{batch.batch_id}/publish", headers=_headers())
    assert r.status_code == 400


def test_publish_dangling_doc_pruned_then_released(env, monkeypatch):
    """doc 已被删除的悬空引用：发布时自动清理（日志留痕），剩余文档正常走完发布。"""
    c, Local = env
    _, batch, docs = _seed_batch(db=Local(), doc_statuses=(DocumentStatus.indexed,))
    with Local() as db:
        db.add(
            KBPublishBatch(
                kb_id=batch.kb_id,
                batch_id=f"{batch.batch_id}-x",
                status=KBBatchStatus.pending,
                doc_ids=[str(batch.doc_ids[0]), str(uuid4())],  # 第二个是悬空 id
            )
        )
        db.commit()
    captured = _stub_eval(monkeypatch)
    flips = _stub_flip(monkeypatch)

    r = c.post(f"{BATCH_API}/{batch.batch_id}-x/publish", headers=_headers())
    assert r.status_code == 202, f"HTTP {r.status_code}: {r.text}"
    item = _wait_batch(c, f"{batch.batch_id}-x", "released")
    assert item["doc_ids"] == [str(docs[0].id)], "悬空引用应被清理"
    assert flips and flips[0][0] == [str(docs[0].id)] and flips[0][1] is True
    assert captured["sample"] == 20, "快检必须 sample=20"


def test_publish_pass_flips_visible_and_releases(env, monkeypatch):
    """主链路：pending → 202 evaluating → 快检 PASS → visible 翻转 + released + 评测留痕。"""
    c, Local = env
    _, batch, docs = _seed_batch(db=Local())
    captured = _stub_eval(monkeypatch, result=[("qa", 0.9, 10, 9), ("refuse", 1.0, 5, 5)])
    flips = _stub_flip(monkeypatch)

    r = c.post(f"{BATCH_API}/{batch.batch_id}/publish", headers=_headers())
    assert r.status_code == 202, f"HTTP {r.status_code}: {r.text}"
    body = r.json()
    assert body["status"] == "evaluating"

    item = _wait_batch(c, batch.batch_id, "released")
    # 快检绑定：sample=20 + 按 kb_id 精确绑定（非同名解析）
    assert captured["sample"] == 20
    assert captured["kb_id"] is not None
    # 翻转：publish 按批次一次 set_payload（batch_tag 过滤），doc 全量 id 传入
    assert flips, "PASS 后必须翻转 visible"
    assert flips[0][1] is True and flips[0][2] == batch.batch_id
    assert set(flips[0][0]) == {str(d.id) for d in docs}

    with Local() as db:
        refreshed = db.get(KBPublishBatch, batch.id)
        assert refreshed.status == KBBatchStatus.released
        assert refreshed.eval_result_id is not None
        rows = (
            db.query(EvalResult)
            .filter_by(source="publish")
            .order_by(EvalResult.created_at)
            .all()
        )
        assert rows, "快检结果必须落 EvalResult（source=publish）"
        assert all(r.status == EvalStatus.DONE for r in rows)
        assert all(r.kb_version for r in rows), "快检行必须绑定 kb_version"
        assert {r.run_id for r in rows} == {
            db.get(EvalResult, refreshed.eval_result_id).run_id
        }, "eval_result_id 锚点必须指向本次运行的 run_id"
    # 列表端点给出评测摘要
    assert item["eval"] is not None
    assert item["eval"]["passed"] is True
    assert any(m["metric"] == "qa" for m in item["eval"]["metrics"])
    # 审计留痕
    with Local() as db:
        actions = {a.action for a in db.query(AuditLog).all()}
        assert "kb.batch.publish" in actions


def test_publish_fail_keeps_staged_and_notifies(env, monkeypatch):
    """快检 FAIL：不翻转（保持 staged 不可见）+ status=failed + admin 通知。"""
    c, Local = env
    _, batch, _ = _seed_batch(db=Local())
    _stub_eval(monkeypatch, result=[("qa", 0.5, 10, 5), ("refuse", 1.0, 5, 5)])
    flips = _stub_flip(monkeypatch)

    r = c.post(f"{BATCH_API}/{batch.batch_id}/publish", headers=_headers())
    assert r.status_code == 202
    _wait_batch(c, batch.batch_id, "failed")
    assert flips == [], "FAIL 不得翻转 visible"

    with Local() as db:
        n = (
            db.query(Notification)
            .filter_by(event_type="kb.batch.failed", recipient_role="admin")
            .all()
        )
        assert n, "FAIL 必须发坐席（admin）通知"
        rows = db.query(EvalResult).filter_by(source="publish").all()
        assert rows and any(r.score == 0.5 for r in rows), "FAIL 分数也必须留痕"


def test_publish_eval_exception_marks_failed_with_failed_row(env, monkeypatch):
    """快检执行异常：FAILED 留痕行（绑定版本语义）+ 批次 failed + 通知，不静默。"""
    c, Local = env
    _, batch, _ = _seed_batch(db=Local())
    _stub_eval(monkeypatch, exc=RuntimeError("LLM 欠费"))
    flips = _stub_flip(monkeypatch)

    r = c.post(f"{BATCH_API}/{batch.batch_id}/publish", headers=_headers())
    assert r.status_code == 202
    _wait_batch(c, batch.batch_id, "failed")
    assert flips == []
    with Local() as db:
        failed_rows = (
            db.query(EvalResult).filter_by(source="publish", status=EvalStatus.FAILED).all()
        )
        assert failed_rows, "异常必须落 FAILED 留痕行"
        assert db.query(Notification).filter_by(event_type="kb.batch.failed").count() >= 1


def test_publish_conflict_409_on_evaluating_and_released(env, monkeypatch):
    """并发/重复 publish：evaluating 409；released 409（先回滚再重发）。"""
    c, Local = env
    _, batch_ev, _ = _seed_batch(db=Local(), status=KBBatchStatus.evaluating)
    _, batch_rel, _ = _seed_batch(db=Local(), status=KBBatchStatus.released)
    assert c.post(f"{BATCH_API}/{batch_ev.batch_id}/publish", headers=_headers()).status_code == 409
    assert (
        c.post(f"{BATCH_API}/{batch_rel.batch_id}/publish", headers=_headers()).status_code == 409
    )


@pytest.mark.parametrize("start", [KBBatchStatus.failed, KBBatchStatus.rolled_back])
def test_republish_allowed_from_failed_and_rolled_back(env, monkeypatch, start):
    """failed / rolled_back 可重新发布（重跑快检；翻转幂等）。"""
    c, Local = env
    _, batch, _ = _seed_batch(db=Local(), status=start)
    _stub_eval(monkeypatch)
    _stub_flip(monkeypatch)
    r = c.post(f"{BATCH_API}/{batch.batch_id}/publish", headers=_headers())
    assert r.status_code == 202, f"从 {start} 重发布应允许: {r.text}"
    _wait_batch(c, batch.batch_id, "released")


# ---------------------------------------------------------------------------
# rollback / list
# ---------------------------------------------------------------------------


def test_rollback_flips_visible_false(env, monkeypatch):
    """released → rollback：visible=False 翻转（batch_tag 恢复 staged 标记）+ rolled_back。"""
    c, Local = env
    _, batch, docs = _seed_batch(db=Local(), status=KBBatchStatus.released)
    flips = _stub_flip(monkeypatch)

    r = c.post(f"{BATCH_API}/{batch.batch_id}/rollback", headers=_headers())
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
    assert r.json()["status"] == "rolled_back"
    assert flips and flips[0][1] is False and flips[0][2] == batch.batch_id
    assert set(flips[0][0]) == {str(d.id) for d in docs}
    with Local() as db:
        assert db.get(KBPublishBatch, batch.id).status == KBBatchStatus.rolled_back
        actions = {a.action for a in db.query(AuditLog).all()}
        assert "kb.batch.rollback" in actions


def test_rollback_requires_released_409(env):
    c, Local = env
    for s in (
        KBBatchStatus.pending,
        KBBatchStatus.evaluating,
        KBBatchStatus.failed,
        KBBatchStatus.rolled_back,
    ):
        _, batch, _ = _seed_batch(db=Local(), status=s)
        r = c.post(f"{BATCH_API}/{batch.batch_id}/rollback", headers=_headers())
        assert r.status_code == 409, f"{s} 回滚应 409，实际 {r.status_code}"


def test_rollback_unknown_batch_404_and_auth(env):
    c, _ = env
    assert c.post(f"{BATCH_API}/ghost/rollback", headers=_headers()).status_code == 404
    assert (
        c.post(f"{BATCH_API}/ghost/rollback", headers=_headers("user")).status_code == 403
    )


def test_list_batches_with_eval_summary(env, monkeypatch):
    """列表端点：全量批次 + status/doc 数/评测摘要（锚点 run_id 聚合全指标）。"""
    c, Local = env
    kb, batch, docs = _seed_batch(db=Local(), status=KBBatchStatus.released)
    anchor = EvalResult(
        run_id="pub-run-1",
        metric="qa",
        score=0.9,
        total=10,
        passed=9,
        status=EvalStatus.DONE,
        source="publish",
        kb_version="2:2026-08-30",
    )
    with Local() as db:
        db.add(anchor)
        db.commit()
        db.add(
            EvalResult(
                run_id="pub-run-1",
                metric="refuse",
                score=1.0,
                total=5,
                passed=5,
                status=EvalStatus.DONE,
                source="publish",
                kb_version="2:2026-08-30",
            )
        )
        b = db.get(KBPublishBatch, batch.id)
        b.eval_result_id = anchor.id
        db.commit()
    # 再造一个 pending 批次（无评测）
    _seed_batch(db=Local(), status=KBBatchStatus.pending)

    r = c.get(BATCH_API, headers=_headers())
    assert r.status_code == 200
    items = {i["batch_id"]: i for i in r.json()["items"]}
    assert len(items) == 2
    rel = items[batch.batch_id]
    assert rel["status"] == "released"
    assert rel["doc_count"] == len(docs)
    assert rel["kb_name"] == kb.name
    assert rel["eval"]["run_id"] == "pub-run-1"
    assert rel["eval"]["passed"] is True
    assert {m["metric"] for m in rel["eval"]["metrics"]} == {"qa", "refuse"}
    pend = [i for i in items.values() if i["status"] == "pending"][0]
    assert pend["eval"] is None


def test_upload_to_evaluating_or_released_batch_rejected(env, monkeypatch):
    """门禁完整性：评测中/已发布批次不接受追加（否则新文档绕过快检被 batch_tag 翻转连带发布）。"""
    c, Local = env
    monkeypatch.setattr("app.workers.import_worker.import_document_task", _FakeTask())
    for s in (KBBatchStatus.evaluating, KBBatchStatus.released):
        kb = c.post(
            f"{API}/knowledge-bases", json={"name": f"库-{s.value}"}, headers=_headers()
        ).json()["kb_id"]
        with Local() as db:
            kbo = db.query(KnowledgeBase).filter_by(name=f"库-{s.value}").one()
            db.add(
                KBPublishBatch(
                    kb_id=kbo.id, batch_id=f"b-{s.value}", status=s, doc_ids=[]
                )
            )
            db.commit()
        r = c.post(
            f"{API}/knowledge-bases/{kb}/documents?batch_id=b-{s.value}",
            files={"file": ("late.md", "迟来内容".encode(), "text/markdown")},
            headers=_headers(),
        )
        assert r.status_code == 400, f"{s.value} 批次追加应 400: {r.text}"


def test_upload_to_evaluating_batch_400_no_zombie_doc(env, monkeypatch):
    """G2 审查 Important-1：批次登记被拒（evaluating 拒追加）→ 400 且不留 parsing 僵尸文档。"""
    from app.models.knowledge import Document
    from app.services.kb_publish_service import KBBatchStatus

    c, Local = env
    kb_id = c.post(
        f"{API}/knowledge-bases", json={"name": "僵尸防护库"}, headers=_headers()
    ).json()["kb_id"]
    fake_task = _FakeTask()
    monkeypatch.setattr("app.workers.import_worker.import_document_task", fake_task)

    # 第一篇正常入 pending 批次
    r1 = c.post(
        f"{API}/knowledge-bases/{kb_id}/documents?batch_id=b-zom",
        files={"file": ("第一批.md", "内容一".encode(), "text/markdown")},
        headers=_headers(),
    )
    assert r1.status_code == 201

    # 手工置 evaluating（模拟发布中）
    with Local() as db:
        db.query(KBPublishBatch).filter_by(batch_id="b-zom").update(
            {"status": KBBatchStatus.evaluating}
        )
        db.commit()

    # 第二篇上传 → 400
    r2 = c.post(
        f"{API}/knowledge-bases/{kb_id}/documents?batch_id=b-zom",
        files={"file": ("第二批.md", "内容二".encode(), "text/markdown")},
        headers=_headers(),
    )
    assert r2.status_code == 400

    # 无僵尸文档：除第一篇外无新增 Document
    from uuid import UUID

    with Local() as db:
        docs = db.query(Document).filter_by(kb_id=UUID(kb_id)).all()
        assert len(docs) == 1, f"留了 {len(docs)} 篇文档（应为 1）"
