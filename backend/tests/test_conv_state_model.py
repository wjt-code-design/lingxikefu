"""Session.conv_state 列测试（批次B）：JSON 可读写、默认 None、与既有列共存。"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.models.base import Base
from app.models.session import Session
from app.models.user import User
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, Session.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    with Local() as s:
        yield s


def test_conv_state_default_none(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email="t@t.local", password_hash="x", status="active"))
    s = Session(user_id=uid)
    db.add(s)
    db.commit()
    db.refresh(s)
    assert s.conv_state is None  # 新会话默认无状态


def test_conv_state_roundtrip(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email="t2@t.local", password_hash="x", status="active"))
    s = Session(user_id=uid)
    s.conv_state = {"stage": "info_collecting", "topic": "退款", "slots": {"order_no": "SO2026080118"}, "clarify_count": 0}
    db.add(s)
    db.commit()
    got = db.scalar(select(Session).where(Session.id == s.id))
    assert got.conv_state["topic"] == "退款"
    assert got.conv_state["slots"]["order_no"] == "SO2026080118"
    # 更新（chat 层每轮写回的场景）
    got.conv_state = {**got.conv_state, "stage": "resolving"}
    db.commit()
    db.refresh(got)
    assert got.conv_state["stage"] == "resolving"


def test_conv_state_column_type_is_json():
    col = Session.__table__.columns["conv_state"]
    assert isinstance(col.type, sa.JSON)
    assert col.nullable is True
