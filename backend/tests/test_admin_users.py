"""Admin 用户列表 API 测试（UI 审查中7）：keyword 搜索（email/phone 模糊命中）。"""
from __future__ import annotations

import uuid

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ALICE = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
BOB = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _admin_h():
    return {"Authorization": f"Bearer {create_access_token(str(ADMIN), 'admin')}"}


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(User(id=ADMIN, email="admin@b.com", role=UserRole.admin, tenant_id="default", password_hash="x"))
        db.add(User(id=ALICE, email="alice@b.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(User(id=BOB, email="carol@b.com", phone="13800001111", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_list_users_pagination(client):
    r = client.get(f"{API}/admin/users", headers=_admin_h())
    assert r.status_code == 200 and r.json()["total"] == 3


def test_list_users_keyword_email(client):
    """keyword 按邮箱模糊命中。"""
    r = client.get(f"{API}/admin/users?keyword=alice", headers=_admin_h())
    body = r.json()
    assert r.status_code == 200 and body["total"] == 1
    assert body["items"][0]["account"] == "alice@b.com"


def test_list_users_keyword_phone(client):
    """keyword 按手机号模糊命中（account 展示仍按 email 优先的既有语义）。"""
    r = client.get(f"{API}/admin/users?keyword=1380000", headers=_admin_h())
    body = r.json()
    assert r.status_code == 200 and body["total"] == 1
    assert body["items"][0]["account"] == "carol@b.com"


def test_list_users_keyword_no_hit(client):
    r = client.get(f"{API}/admin/users?keyword=nonexistent", headers=_admin_h())
    assert r.json()["total"] == 0


def test_list_users_forbidden_for_non_admin(client):
    h = {"Authorization": f"Bearer {create_access_token(str(ALICE), 'user')}"}
    assert client.get(f"{API}/admin/users", headers=h).status_code == 403
