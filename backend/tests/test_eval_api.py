"""后台评测中心接线测试（P1）：锁定 eval.py 依赖的 run_faithfulness_eval / run_recall_eval 存在，
_do_eval 同时写入 faithfulness + recall 两组指标，且 HTTP 端点 POST /admin/eval/run
能真实触发后台任务并落表（show-your-work：消除"只测 _do_eval 未走完整端点"的已知限制）。

红测背景：eval.py 的 _do_eval 引用 `from scripts.eval_faithfulness import run_faithfulness_eval`，
但 eval_faithfulness.py 从未定义该函数 → import 必然 ImportError → 被外层 except 吞掉 →
POST /admin/eval/run 后台静默失败，EvalResult 表零写入（后台评测中心实际不可用）。
recall（检索召回）同样从未接入后台评测中心。
"""
from __future__ import annotations

import asyncio
import builtins
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.api.eval import _do_eval
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.eval_result import EvalResult, EvalStatus
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _h(uid: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(uid), role=role)}"}


def test_faithfulness_script_exports_run_faithfulness_eval():
    """eval.py 依赖的可复用入口必须存在（当前 ImportError → 红测锁定）。"""
    from scripts.eval_faithfulness import run_faithfulness_eval

    assert callable(run_faithfulness_eval)


def test_recall_script_exports_run_recall_eval():
    """recall 接入所需的可复用入口必须存在（当前 AttributeError → 红测锁定）。"""
    from scripts.eval_recall import run_recall_eval

    assert callable(run_recall_eval)


@pytest.mark.asyncio
async def test_do_eval_writes_faithfulness_and_recall(monkeypatch):
    """_do_eval 应同时落 faithfulness 与 recall 两组指标（当前零 recall → 红测锁定）。"""
    written: list[EvalResult] = []

    class _FakeDB:
        def add(self, obj):
            written.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _FakeDB())

    async def _fake_faithfulness(db, limit=0, kb_name=None):
        return [("faithfulness", 0.9, 10, 9), ("refuse", 1.0, 8, 8)]

    def _fake_recall(db, limit=0, kb_name=None, top_k=5):  # 同步：与真实 run_recall_eval 一致（eval.py 走 to_thread）
        return [("recall", 0.88, 80, 70), ("honesty", 0.0, 8, 8)]

    monkeypatch.setattr(
        "scripts.eval_faithfulness.run_faithfulness_eval", _fake_faithfulness, raising=False
    )
    monkeypatch.setattr("scripts.eval_recall.run_recall_eval", _fake_recall, raising=False)

    await _do_eval("test-run-1")

    metrics = {r.metric for r in written}
    assert "faithfulness" in metrics, f"faithfulness 未写入: {[r.metric for r in written]}"
    # 防假绿：recall 必须是 DONE 且 score 精确（若走了 except 会写 FAILED/score=0，此处即红）
    recall_rows = [r for r in written if r.metric == "recall"]
    assert recall_rows, f"recall 未写入: {[r.metric for r in written]}"
    assert recall_rows[0].status == EvalStatus.DONE, (
        f"recall 走了 FAILED 分支（fake 未被执行）: status={recall_rows[0].status}"
    )
    assert recall_rows[0].score == 0.88, f"recall score 异常: {recall_rows[0].score}"


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[EvalResult.__table__, User.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(
            User(
                id=ADMIN,
                email="admin@b.com",
                role=UserRole.admin,
                tenant_id="default",
                password_hash="x",
            )
        )
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


def test_eval_run_endpoint_triggers_background_and_writes_recall(client, monkeypatch):
    """端到端：POST /admin/eval/run → create_task 后台任务真实执行 → EvalResult 双指标落表。

    show-your-work：走完整 HTTP 端点（含 require_admin 鉴权 + 异步触发），
    轮询等待后台任务完成；若 create_task 未执行或走了 except FAILED 分支则红。
    """
    written: list[EvalResult] = []

    class _FakeDB:
        def add(self, obj):
            written.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _FakeDB())

    async def _fake_faithfulness(db, limit=0, kb_name=None):
        return [("faithfulness", 0.9, 10, 9), ("refuse", 1.0, 8, 8)]

    def _fake_recall(db, limit=0, kb_name=None, top_k=5):  # 同步：与真实一致（to_thread）
        return [("recall", 0.88, 80, 70), ("honesty", 0.0, 8, 8)]

    monkeypatch.setattr(
        "scripts.eval_faithfulness.run_faithfulness_eval", _fake_faithfulness, raising=False
    )
    monkeypatch.setattr("scripts.eval_recall.run_recall_eval", _fake_recall, raising=False)

    r = client.post(f"{API}/admin/eval/run", json={}, headers=_h(ADMIN, "admin"))
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
    run_id = r.json()["run_id"]
    assert run_id, "响应缺少 run_id"

    # 后台 create_task 在 TestClient portal 事件循环执行 → 主线程轮询等待落表
    deadline = time.time() + 8
    while time.time() < deadline and not written:
        time.sleep(0.1)

    metrics = {x.metric for x in written}
    assert "faithfulness" in metrics, (
        f"后台任务未执行或 faithfulness 未落表（run_id={run_id}）: {[x.metric for x in written]}"
    )
    assert "recall" in metrics, f"recall 未落表: {[x.metric for x in written]}"
    recall_rows = [x for x in written if x.metric == "recall"]
    assert recall_rows[0].status == EvalStatus.DONE, (
        f"recall 走了 FAILED 分支（后台任务未真实执行 run_recall_eval）: status={recall_rows[0].status}"
    )
    assert recall_rows[0].score == 0.88, f"recall score 异常: {recall_rows[0].score}"


# --- P3-⑭ eval 三连 ---------------------------------------------------------


def test_eval_history_aggregates_by_run_id():
    """P3-⑭①：/eval/history 按 run_id 聚合最近 30 次运行，同一 run 的指标行不分离。

    31 次运行 × 2 指标 = 62 行；聚合后仅 30 个 run_id，最新 run 的两行都保留。
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[EvalResult.__table__, User.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    try:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        with Local() as db:
            db.add(
                User(
                    id=ADMIN,
                    email="admin@b.com",
                    role=UserRole.admin,
                    tenant_id="default",
                    password_hash="x",
                )
            )
            for i in range(31):
                run_id = f"run-{i:02d}"
                created = base + timedelta(seconds=i)
                db.add(
                    EvalResult(
                        run_id=run_id, metric="faithfulness", score=0.8,
                        total=10, passed=8, status=EvalStatus.DONE,
                        source="manual", created_at=created,
                    )
                )
                db.add(
                    EvalResult(
                        run_id=run_id, metric="recall", score=0.7,
                        total=50, passed=35, status=EvalStatus.DONE,
                        source="manual", created_at=created,
                    )
                )
            db.commit()
        with TestClient(app) as c:
            r = c.get(f"{API}/admin/eval/history", headers=_h(ADMIN, "admin"))
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
        items = r.json()["items"]
        run_ids = {i["run_id"] for i in items}
        assert len(run_ids) == 30, f"聚合后应 30 个 run_id，实际 {len(run_ids)}"
        assert "run-00" not in run_ids, "最旧运行不应越过 30 次窗口"
        assert "run-30" in run_ids, "最新运行必须包含"
        newest = [i for i in items if i["run_id"] == "run-30"]
        assert len(newest) == 2, f"同一 run 的指标行被分离: {len(newest)}"
        assert {i["metric"] for i in newest} == {"faithfulness", "recall"}
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_run_eval_holds_task_ref_until_done(client, monkeypatch):
    """P3-⑭②：create_task 引用必须挂模块级集合，任务完成才移除（防 GC 中杀 + 防泄漏）。

    用 threading.Event 门控 _do_eval（经 to_thread 等待，跨线程唤醒语义安全），
    锁定任务"执行中存在于集合 / 完成后被 discard"两个时相。
    """
    from app.api import eval as eval_module

    release = threading.Event()

    async def _fake_do_eval(run_id, limit=0, kb_name=None):
        await asyncio.to_thread(release.wait, 10)

    monkeypatch.setattr("app.api.eval._do_eval", _fake_do_eval)

    r = client.post(f"{API}/admin/eval/run", json={}, headers=_h(ADMIN, "admin"))
    assert r.status_code == 200, f"HTTP {r.status_code}: {r.text}"
    run_id = r.json()["run_id"]

    def _held() -> bool:
        return any(t.get_name() == f"eval-{run_id}" for t in eval_module._eval_tasks)

    # 任务被 release 门控 → 引用必仍在集合中
    deadline = time.time() + 5
    while time.time() < deadline and not _held():
        time.sleep(0.02)
    assert _held(), "任务执行期间引用不在集合（GC 中杀风险未消除）"

    release.set()
    # 放行后任务完成 → done_callback 移除引用
    deadline = time.time() + 8
    while time.time() < deadline and _held():
        time.sleep(0.02)
    assert not _held(), "任务完成后引用未移除（集合泄漏）"


@pytest.mark.asyncio
async def test_do_eval_import_failure_writes_failed_record(monkeypatch):
    """P3-⑭③：scripts 导入失败 → 落 FAILED 记录并日志明示，不阻塞另一阶段。"""
    written: list[EvalResult] = []

    class _FakeDB:
        def add(self, obj):
            written.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _FakeDB())

    # recall 阶段用 fake（真函数会按真实 db 查 KB，_FakeDB 无 scalar 会误判为本阶段失败）
    def _fake_recall(db, limit=0, kb_name=None, top_k=5):
        return [("recall", 0.88, 80, 70)]

    monkeypatch.setattr(
        "scripts.eval_faithfulness.run_faithfulness_eval", _fake_recall, raising=False
    )
    monkeypatch.setattr("scripts.eval_recall.run_recall_eval", _fake_recall, raising=False)

    orig_import = builtins.__import__

    def _raising_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.startswith("scripts.eval_faithfulness"):
            raise ImportError(f"No module named '{name}'")
        return orig_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _raising_import)

    await _do_eval("test-import-fail")

    failed = [r for r in written if r.status == EvalStatus.FAILED]
    assert failed, f"导入失败未落 FAILED 记录: {[r.metric for r in written]}"
    assert any(r.metric == "faithfulness" for r in failed), (
        f"FAILED 记录缺失 faithfulness: {[r.metric for r in written]}"
    )
    recall_done = [r for r in written if r.metric == "recall" and r.status == EvalStatus.DONE]
    assert recall_done, (
        f"faithfulness 模块缺失不应阻塞 recall 阶段: {[r.metric for r in written]}"
    )


def test_eval_run_rejects_concurrent_trigger(client, monkeypatch):
    """并发守护：已有评测任务在跑时再次触发 → 409（防重复点击叠加跑）。

    2026-08-31 实测缺陷：run_eval 无并发检查，前端连点 3 次 = 3 个全量评测
    并发（6 stage ≈ 300 次 LongCat 调用），限速下互相拖慢且可能触发 402 欠费。
    守护语义：_eval_tasks 非空 → 409；任务完成移除引用后恢复可触发。
    """
    from app.api import eval as eval_module

    release = threading.Event()

    async def _fake_do_eval(run_id, limit=0, kb_name=None):
        await asyncio.to_thread(release.wait, 10)

    monkeypatch.setattr("app.api.eval._do_eval", _fake_do_eval)

    r1 = client.post(f"{API}/admin/eval/run", json={}, headers=_h(ADMIN, "admin"))
    assert r1.status_code == 200, f"首次触发应成功: HTTP {r1.status_code}"

    # 任务仍在集合中（门控未放行）→ 第二次触发必须被拒
    r2 = client.post(f"{API}/admin/eval/run", json={}, headers=_h(ADMIN, "admin"))
    assert r2.status_code == 409, f"并发触发未被拒绝: HTTP {r2.status_code} {r2.text}"
    # 全局异常处理器把 HTTPException 包装为 {code, message, request_id}（非 FastAPI 裸 detail）
    assert "评测" in r2.json().get("message", "")

    release.set()
    # 第一个任务完成、引用移除后 → 恢复可触发（守护不得永久锁死）
    deadline = time.time() + 8
    while time.time() < deadline and eval_module._eval_tasks:
        time.sleep(0.02)
    r3 = client.post(f"{API}/admin/eval/run", json={}, headers=_h(ADMIN, "admin"))
    assert r3.status_code == 200, f"任务完成后应恢复可触发: HTTP {r3.status_code}"
