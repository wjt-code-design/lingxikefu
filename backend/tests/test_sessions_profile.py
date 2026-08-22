"""会话详情用户画像测试（2026-08-22 Phase D 客服侧展示）：

- agent/admin 查看会话 → 详情返回用户画像（topics/entities/satisfaction/handoff）；
- 顾客（owner user）查看自己会话 → profile 为 None（不泄露他人/冗余画像）；
- 他人 user 查看 → 403（越权读防护，既有行为回归）。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.message import Message
from app.models.session import Session
from app.models.user import User, UserRole
from app.models.user_profile import UserProfile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
UID = uuid.UUID("11111111-1111-1111-1111-111111111111")  # 会话 owner
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")  # 其他普通用户
AGENT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("99999999-9999-9999-9999-999999999999")


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # JSONB → SQLite 兼容
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    for c in UserProfile.__table__.columns:
        if c.name == "profile":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine,
        tables=[
            User.__table__,
            Session.__table__,
            Message.__table__,
            UserProfile.__table__,
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
    with Local() as db:
        db.add(User(id=UID, email="u@t.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(User(id=OTHER, email="o@t.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(User(id=AGENT, email="a@t.com", role=UserRole.agent, tenant_id="default", password_hash="x"))
        db.add(Session(id=SID, user_id=UID, tenant_id="default", title="画像会话"))
        # Phase B 采集的画像：写入 owner（UID）的画像
        db.add(
            UserProfile(
                user_id=UID,
                tenant_id="default",
                profile={
                    "schema_version": 1,
                    "topics": {"退款": 3, "物流": 1},
                    "entities": ["SO2026080118"],
                    "satisfaction": {"up": 2, "down": 0},
                    "handoff": {"count": 2},
                    "preferences": {"品类": ["洗衣机"]},
                },
                version=1,
            )
        )
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _h(uid: uuid.UUID, role: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(uid), role)}"}


def test_get_session_profile_visible_to_agent(client):
    """客服查看会话 → 详情返回用户画像。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_h(AGENT, "agent"))
    assert r.status_code == 200
    body = r.json()
    assert body["profile"] is not None
    assert body["profile"]["topics"]["退款"] == 3
    assert "SO2026080118" in body["profile"]["entities"]
    assert body["profile"]["satisfaction"]["up"] == 2
    assert body["profile"]["handoff"]["count"] == 2


def test_get_session_profile_null_for_owner(client):
    """顾客查看自己会话 → profile 为 None（不泄露画像给他本人）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_h(UID, "user"))
    assert r.status_code == 200
    # 顾客端 profile 应为 None（画像仅供客服侧接待参考）
    assert r.json()["profile"] is None


def test_get_session_foreign_user_403(client):
    """其他普通用户查看他人会话 → 403（越权读防护回归）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_h(OTHER, "user"))
    assert r.status_code == 403
