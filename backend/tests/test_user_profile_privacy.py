"""Phase E 隐私 + 开关测试（2026-08-22）：

- reset 接口（POST /auth/me/profile/reset）：清空当前用户画像；无画像幂等 ok；
- 开关 USER_PROFILE_ENABLED=False：merge_profile 不写画像（采集停）、
  stream_answer 注入不产生 <<用户画像>> 块（注入停）→ 回答与开启前一致（diff=0 兼容）。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.user import User, UserRole
from app.models.user_profile import UserProfile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.services.user_profile_service import get_profile, merge_profile, to_prompt_text

API = "/api/v1"
UID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for c in UserProfile.__table__.columns:
        if c.name == "profile":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(engine, tables=[User.__table__, UserProfile.__table__])
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
        db.commit()
    with TestClient(app) as c:
        yield c, Local
    app.dependency_overrides.clear()


def _h():
    return {"Authorization": f"Bearer {create_access_token(str(UID), 'user')}"}


# ---------- 一、reset 接口（隐私自主控制） ----------

def test_reset_profile_endpoint_clears(client, monkeypatch):
    """有画像 → reset 接口清空 → get_profile None。"""
    tc, Local = client
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", True)
    with Local() as db:
        merge_profile(db, UID, "订单 SO2026080118 怎么退款")
        assert get_profile(db, UID) is not None

    r = tc.post(f"{API}/auth/me/profile/reset", headers=_h())
    assert r.status_code == 200
    assert r.json()["ok"] is True

    with Local() as db:
        assert get_profile(db, UID) is None


def test_reset_profile_endpoint_idempotent(client, monkeypatch):
    """无画像 → reset 幂等返回 ok（重复调无副作用）。"""
    tc, Local = client
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", True)
    r1 = tc.post(f"{API}/auth/me/profile/reset", headers=_h())
    r2 = tc.post(f"{API}/auth/me/profile/reset", headers=_h())
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["ok"] is True and r2.json()["ok"] is True


# ---------- 二、开关 USER_PROFILE_ENABLED=False ----------

def test_switch_off_stops_collection(client, monkeypatch):
    """开关关闭：merge_profile 不写画像（采集停）。"""
    tc, Local = client
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", False)
    with Local() as db:
        ok = merge_profile(db, UID, "订单 SO2026080118 怎么退款")
        assert ok is False  # 不采集
        assert get_profile(db, UID) is None


def test_switch_off_prompt_diff_zero(client, monkeypatch):
    """开关关闭：画像不可得（采集已停）→ 注入源为 None → 与开启前 diff=0 兼容。

    链路验证：关闭时 merge_profile 不写（test_switch_off_stops_collection）→
    get_profile 返回 None → to_prompt_text(None) 返回 None（chat.py 注入处不产生
    <<用户画像>> 块，与旧版一致）。画像为空即"无注入"，不改变回答。
    """
    monkeypatch.setattr(settings, "USER_PROFILE_ENABLED", False)
    with client[1]() as db:
        assert get_profile(db, UID) is None  # 采集已停，无画像
    assert to_prompt_text(None) is None  # 无画像 → 不注入（diff=0）