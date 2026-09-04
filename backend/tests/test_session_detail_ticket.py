"""D3：会话详情回传最新工单 → 前端刷新后恢复「转人工/工单」气泡。

背景：手动转人工的工单气泡是前端本地 state（manualTicket），刷新即丢；
历史加载的 getSessionDetail 不含工单信息 → 用户重进会话看不到工单进度。
工单是会话级实体（转人工不产生消息，无法挂 message.meta），
故修复形态 = SessionDetail 增 ticket 字段（该会话最新一张工单）。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.message import Message, MessageSource
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")

_engine = None
_Local = None


@pytest.fixture
def client():
    global _engine, _Local
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        _engine,
        tables=[Session.__table__, Message.__table__, MessageSource.__table__, Ticket.__table__],
    )
    _Local = sessionmaker(bind=_engine, expire_on_commit=False)

    def _override():
        db = _Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with _Local() as db:
        db.add(Session(id=SID, user_id=USER_ID, title="退货咨询"))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def _insert_ticket(
    status: TicketStatus = TicketStatus.open,
    source: str = "manual",
    updated_at: datetime | None = None,
) -> uuid.UUID:
    """直连 fixture engine 插工单（绕过端点，聚焦读路径断言）。

    updated_at 可显式指定：SQLite CURRENT_TIMESTAMP 秒级精度，同秒插两张会排序
    不定——「取最新」用例必须显式制造时间差（真实场景流转/重开会刷新 updated_at）。
    """
    t = Ticket(tenant_id="default", session_id=SID, status=status, source=source)
    if updated_at is not None:
        t.created_at = updated_at
        t.updated_at = updated_at
    with _Local() as db:
        db.add(t)
        db.commit()
        db.refresh(t)
    return t.id


def test_detail_ticket_none_when_no_ticket(client):
    """无工单会话：detail.ticket 为 None（不报错、不省略键）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_user_h())
    assert r.status_code == 200
    assert r.json()["ticket"] is None


def test_detail_returns_ticket_for_session(client):
    """有工单会话：detail.ticket 回传 {ticket_id, status, source}（前端恢复气泡用）。"""
    tid = _insert_ticket(TicketStatus.open, "manual")
    r = client.get(f"{API}/sessions/{SID}", headers=_user_h())
    body = r.json()["ticket"]
    assert body is not None
    assert body["ticket_id"] == str(tid)
    assert body["status"] == "open"
    assert body["source"] == "manual"


def test_detail_ticket_is_latest_by_updated_at(client):
    """多张工单（历史关闭后重开）：取 updated_at 最新的一张。

    显式制造 1 小时时间差——SQLite created_at/updated_at 是秒级 CURRENT_TIMESTAMP，
    同秒插入排序不定（真实场景流转会刷新 updated_at，不会同秒）。
    """
    now = datetime.now(UTC).replace(microsecond=0)
    _insert_ticket(TicketStatus.closed, "ai", updated_at=now - timedelta(hours=1))
    t2 = _insert_ticket(TicketStatus.processing, "manual", updated_at=now)
    body = client.get(f"{API}/sessions/{SID}", headers=_user_h()).json()["ticket"]
    assert body["ticket_id"] == str(t2)
    assert body["status"] == "processing"


def test_detail_ticket_does_not_leak_other_session(client):
    """越权面：另一会话的工单不得出现在本会话详情。"""
    other_sid = uuid.uuid4()
    with _Local() as db:
        db.add(Session(id=other_sid, user_id=USER_ID))
        db.commit()
    # 工单挂在别的会话上
    with _Local() as db:
        db.add(Ticket(tenant_id="default", session_id=other_sid, status=TicketStatus.open, source="ai"))
        db.commit()
    assert client.get(f"{API}/sessions/{SID}", headers=_user_h()).json()["ticket"] is None
