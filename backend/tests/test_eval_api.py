"""后台评测中心接线测试（P1）：锁定 eval.py 依赖的 run_faithfulness_eval / run_recall_eval 存在，
_do_eval 同时写入 faithfulness + recall 两组指标，且 HTTP 端点 POST /admin/eval/run
能真实触发后台任务并落表（show-your-work：消除"只测 _do_eval 未走完整端点"的已知限制）。

红测背景：eval.py 的 _do_eval 引用 `from scripts.eval_faithfulness import run_faithfulness_eval`，
但 eval_faithfulness.py 从未定义该函数 → import 必然 ImportError → 被外层 except 吞掉 →
POST /admin/eval/run 后台静默失败，EvalResult 表零写入（后台评测中心实际不可用）。
recall（检索召回）同样从未接入后台评测中心。
"""
from __future__ import annotations

import time
import uuid

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
