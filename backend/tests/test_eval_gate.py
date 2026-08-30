"""KB 发布门禁 v1（架构三期 3）：EvalResult 绑定 kb_version + GET /admin/eval/gate 三态观测。

红测背景（plan-facts-p3 B3）：EvalResult 无 kb 版本绑定字段——"评测通过"无法对应到
当前 KB 内容，导完新文档后旧 PASS 仍被当成现行状态。本任务：
- 触发链（POST /admin/eval/run → _do_eval）每阶段完成时把当时 kb_version 写入新列
  （评测脚本本体 eval_faithfulness.py 冻结零改动，只改 admin 侧触发链）；
- GET /admin/eval/gate 一屏可见：{kb_version, last_eval|None, passed}，
  passed 三态（True/False；None=当前版本从未评测——含"有历史评测但绑定旧版本"，不误报）。

观测非阻断：强制阻断导入与自动回滚留 v2，本组用例不改变任何写路径行为。
评测真实调 LLM——统一 monkeypatch scripts 层评测函数打桩（照 test_eval_api 手法）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.eval_result import EvalResult, EvalStatus
from app.models.knowledge import Document, DocumentStatus, KnowledgeBase
from app.models.user import User, UserRole
from app.services import kb_lookup
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
KB_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _h() -> dict[str, str]:
    token = create_access_token(subject=str(ADMIN), role="admin")
    return {"Authorization": f"Bearer {token}"}


def _seed_kb(db) -> KnowledgeBase:
    """建库 + 2 篇 indexed 文档（库名与 smoke_import 一致 → eval _resolve_kb 主路径命中）。"""
    from scripts.smoke_import import _KB_NAME

    kb = KnowledgeBase(id=KB_ID, tenant_id="default", name=_KB_NAME)
    db.add(kb)
    for i in range(2):
        db.add(
            Document(
                kb_id=KB_ID,
                tenant_id="default",
                name=f"doc-{i}",
                status=DocumentStatus.indexed,
                sha256=f"sha-gate-{i}",
                raw_text=f"text {i}",
                chunk_count=1,
            )
        )
    db.commit()
    return kb


def _add_eval_rows(db, run_id: str, kb_version: str | None, qa_score: float, at: datetime | None = None) -> None:
    """落一次评测的指标行（qa + refuse），绑定指定 kb_version（None=旧行不绑定）。"""
    db.add(
        EvalResult(
            run_id=run_id, metric="qa", score=qa_score, total=10,
            passed=round(qa_score * 10), status=EvalStatus.DONE, source="manual",
            kb_version=kb_version, created_at=at or datetime.now(UTC),
        )
    )
    db.add(
        EvalResult(
            run_id=run_id, metric="refuse", score=1.0, total=4, passed=4,
            status=EvalStatus.DONE, source="manual",
            kb_version=kb_version, created_at=at or datetime.now(UTC),
        )
    )
    db.commit()


# --- 触发链：_do_eval 落表时绑定当前 kb_version --------------------------------


async def test_do_eval_binds_current_kb_version(monkeypatch):
    """评测完成落表的 EvalResult.kb_version 非空且等于当前版本指纹（DONE 与 FAILED 留痕行都绑定）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[KnowledgeBase.__table__, Document.__table__, EvalResult.__table__]
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    with Local() as db:
        kb = _seed_kb(db)
        expected = kb_lookup.kb_version_str(db, kb.id)

    monkeypatch.setattr("app.core.database.SessionLocal", Local)

    async def _fake_faithfulness(db, limit=0, kb_name=None):
        return [("qa", 0.9, 10, 9), ("refuse", 1.0, 4, 4)]

    def _fake_recall(db, limit=0, kb_name=None, top_k=5):
        raise RuntimeError("recall 中途挂（锁定失败留痕行同样绑定版本）")

    monkeypatch.setattr(
        "scripts.eval_faithfulness.run_faithfulness_eval", _fake_faithfulness, raising=False
    )
    monkeypatch.setattr("scripts.eval_recall.run_recall_eval", _fake_recall, raising=False)

    from app.api.eval import _do_eval

    await _do_eval("gate-bind-1")

    with Local() as db:
        rows = db.scalars(select(EvalResult)).all()
    assert rows, "评测未落表"
    done = [r for r in rows if r.status == EvalStatus.DONE]
    assert {r.metric for r in done} == {"qa", "refuse"}, f"DONE 指标异常: {[r.metric for r in rows]}"
    assert all(r.kb_version == expected for r in done), (
        f"DONE 行未绑定当前版本: expected={expected!r}, "
        f"actual={[r.kb_version for r in done]}"
    )
    failed = [r for r in rows if r.status == EvalStatus.FAILED]
    assert failed, "recall 阶段失败未留痕（现状行为回退）"
    assert all(r.kb_version == expected for r in failed), (
        f"FAILED 留痕行未绑定当前版本: {[r.kb_version for r in failed]}"
    )


# --- gate 端点三态 -------------------------------------------------------------


@pytest.fixture
def gate_env():
    """独立 SQLite 环境：admin 用户 + KB + indexed 文档；yield session 工厂。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[KnowledgeBase.__table__, Document.__table__, EvalResult.__table__, User.__table__]
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    with Local() as db:
        db.add(
            User(
                id=ADMIN, email="admin@b.com", role=UserRole.admin,
                tenant_id="default", password_hash="x",
            )
        )
        _seed_kb(db)
        db.commit()
    kb_lookup._kb_cache = None  # 模块级 60s TTL 缓存防跨测试污染
    yield Local
    kb_lookup._kb_cache = None
    engine.dispose()


@pytest.fixture
def client(gate_env):
    def _override():
        db = gate_env()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def test_gate_never_evaluated_returns_none_passed(client, gate_env):
    """当前版本从未评测 → kb_version=当前值, last_eval=None, passed=None（不误报）。"""
    with gate_env() as db:
        current = kb_lookup.kb_version_str(db, KB_ID)
    r = client.get(f"{API}/admin/eval/gate", headers=_h())
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
    body = r.json()
    assert body["kb_version"] == current
    assert body["last_eval"] is None
    assert body["passed"] is None


def test_gate_passed_true(client, gate_env):
    """当前版本评测通过（qa≥85% 且 refuse≥90%）→ passed=True + last_eval 带指标明细。"""
    with gate_env() as db:
        current = kb_lookup.kb_version_str(db, KB_ID)
        _add_eval_rows(db, "run-ok", current, qa_score=0.9)
    r = client.get(f"{API}/admin/eval/gate", headers=_h())
    body = r.json()
    assert body["kb_version"] == current
    assert body["passed"] is True
    assert body["last_eval"]["run_id"] == "run-ok"
    metrics = {m["metric"]: m for m in body["last_eval"]["metrics"]}
    assert metrics["qa"]["score"] == 0.9
    assert metrics["qa"]["status"] == EvalStatus.DONE


def test_gate_passed_false(client, gate_env):
    """当前版本评测未达阈值（qa=50% < 85%）→ passed=False。"""
    with gate_env() as db:
        current = kb_lookup.kb_version_str(db, KB_ID)
        _add_eval_rows(db, "run-bad", current, qa_score=0.5)
    r = client.get(f"{API}/admin/eval/gate", headers=_h())
    assert r.json()["passed"] is False


def test_gate_stale_version_returns_none(client, gate_env):
    """有历史评测但绑定的是旧版本 → passed=None（不拿旧版本的 PASS 冒充当前状态）。"""
    stale = "1:2020-01-01T00:00:00+00:00"
    with gate_env() as db:
        _add_eval_rows(db, "run-old", stale, qa_score=1.0, at=datetime(2026, 1, 1, tzinfo=UTC))
    r = client.get(f"{API}/admin/eval/gate", headers=_h())
    body = r.json()
    assert body["last_eval"] is None, f"旧版本评测被误读为当前状态: {body['last_eval']}"
    assert body["passed"] is None


def test_gate_picks_current_version_run_not_latest_pass(client, gate_env):
    """旧版本 PASS + 当前版本 FAIL 并存 → 取当前版本的 run 判 False（不挑最好的说）。"""
    stale = "1:2020-01-01T00:00:00+00:00"
    with gate_env() as db:
        current = kb_lookup.kb_version_str(db, KB_ID)
        _add_eval_rows(db, "run-old", stale, qa_score=1.0, at=datetime(2026, 1, 1, tzinfo=UTC))
        _add_eval_rows(
            db, "run-cur", current, qa_score=0.5, at=datetime(2026, 1, 2, tzinfo=UTC)
        )
    r = client.get(f"{API}/admin/eval/gate", headers=_h())
    body = r.json()
    assert body["passed"] is False, f"未按当前版本最新 run 判定: {body}"
    assert body["last_eval"]["run_id"] == "run-cur"


def test_gate_without_kb_returns_all_none(gate_env):
    """无任何 KB → kb_version=None, last_eval=None, passed=None（空态不炸）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[KnowledgeBase.__table__, EvalResult.__table__, User.__table__]
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    with Local() as db:
        db.add(
            User(
                id=ADMIN, email="admin@b.com", role=UserRole.admin,
                tenant_id="default", password_hash="x",
            )
        )
        db.commit()

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    kb_lookup._kb_cache = None
    try:
        with TestClient(app) as c:
            r = c.get(f"{API}/admin/eval/gate", headers=_h())
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        body = r.json()
        assert body["kb_version"] is None
        assert body["last_eval"] is None
        assert body["passed"] is None
    finally:
        kb_lookup._kb_cache = None
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()


def test_gate_requires_admin(client):
    """非 admin 访问 → 403（require_admin 按令牌角色判，与其它 /admin/eval/* 同一鉴权面）。"""
    token = create_access_token(subject=str(uuid.uuid4()), role="user")
    r = client.get(f"{API}/admin/eval/gate", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, f"HTTP {r.status_code}: {r.text}"
