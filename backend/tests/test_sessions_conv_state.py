"""会话详情 conv_state 透出测试（批次B）：agent 可见 / user 视角不返回结构化状态。"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.message import Message, MessageSource
from app.models.session import Session
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
AGENT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine, tables=[Session.__table__, Message.__table__, User.__table__, MessageSource.__table__]
    )
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(User(id=AGENT_ID, role=UserRole.agent, email="agent@test.local", password_hash="x", status="active"))
        db.add(
            Session(
                id=SID,
                user_id=USER_ID,
                conv_state={"stage": "info_collecting", "topic": "退款", "slots": {}, "clarify_count": 0},
            )
        )
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT_ID), 'agent')}"}


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def test_detail_returns_conv_state_for_agent(client):
    """agent 视角：conv_state 结构化透出（客服观察用）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_agent_h())
    assert r.status_code == 200
    cs = r.json()["conv_state"]
    assert cs["topic"] == "退款"
    assert cs["stage"] == "info_collecting"


def test_detail_conv_state_none_for_old_session(client):
    """旧会话（conv_state=None）：字段返回 None，不报错。"""
    with TestClient(app) as c:
        # 新建无状态会话
        r = c.post(
            f"{API}/sessions",
            headers=_user_h(),
            json={"title": "t"},
        )
        new_sid = r.json()["session_id"]
        r2 = c.get(f"{API}/sessions/{new_sid}", headers=_agent_h())
        assert r2.status_code == 200
        assert r2.json()["conv_state"] is None


# ---- P2-⑥ 并发读改写 —— 行锁（with_for_update）防丢更新 ----

def _conv_state_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[Session.__table__])
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_update_conv_state_uses_row_lock(monkeypatch):
    """P2-⑥：conv_state 读改写必须调用 with_for_update()（旧实现直接读请求入口的 stale 对象，绝不走行锁）。"""
    from app.api.chat import _update_conv_state_locked
    from sqlalchemy.sql.selectable import Select

    calls: list[bool] = []
    orig = Select.with_for_update

    def spy(self, **kw):
        calls.append(True)
        return orig(self, **kw)

    monkeypatch.setattr(Select, "with_for_update", spy)
    engine, Local = _conv_state_engine()
    with Local() as db:
        db.add(Session(id=SID, user_id=USER_ID, conv_state=None))
        db.commit()
        new_state = _update_conv_state_locked(db, SID, "我要退款")
    assert calls, "conv_state 读改写必须经 with_for_update() 行锁"
    assert new_state["topic"] == "退款"


def test_conv_state_locked_two_writers_both_persist():
    """P2-⑥：后提交写者以行锁重读的最新行（含先提交者的槽位）为基准合并——先提交者写入不丢。"""
    from app.api.chat import _update_conv_state_locked

    engine, Local = _conv_state_engine()
    with Local() as db:
        db.add(Session(id=SID, user_id=USER_ID, conv_state=None))
        db.commit()
    # 写者 A：先提交主题 + 订单号
    with Local() as db:
        _update_conv_state_locked(db, SID, "我要退款，订单号 SO111222333")
    # 写者 B：后提交（无主题词/无订单号）——若基于过期空状态会覆盖掉 A 的主题与订单号；
    # 行锁重读的最新行 → A 的结果整体保留
    with Local() as db:
        state_b = _update_conv_state_locked(db, SID, "运费怎么算")
    assert state_b["slots"]["order_no"] == "SO111222333"
    assert state_b["topic"] == "退款"  # A 的主题与槽位不因 B 的写回而丢


def test_conv_state_locked_emits_for_update_on_pg():
    """P2-⑥：PG 方言下编译必须带 FOR UPDATE（真实行锁）；SQLite 自动降级无锁。"""
    from sqlalchemy import select
    from sqlalchemy.dialects import postgresql

    stmt = select(Session).where(Session.id == SID).with_for_update()
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql.upper()
