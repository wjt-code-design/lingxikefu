"""Suggestion 意见反馈端点测试（P2-修复#2）：POST/GET /suggestions。

覆盖：
- 无凭证 → 401；
- user 提交 → 201 + 落库（type/content/contact）；
- content 空 → 422；
- user 拉列表 → 403（仅 admin）；
- admin 列表 → 200，含提交内容与提交人账号。
限流路径依赖 RATE_LIMIT_ENABLED（测试环境关闭，不覆盖）。
"""
from __future__ import annotations

import uuid

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.feedback import Suggestion
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

USER_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
ADMIN_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, Suggestion.__table__])
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
                id=USER_ID,
                role=UserRole.user,
                email="fb-user@test.local",
                password_hash="x",
                status="active",
            )
        )
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def _admin_h():
    return {"Authorization": f"Bearer {create_access_token(str(ADMIN_ID), 'admin')}"}


def test_submit_requires_auth(client):
    r = client.post(f"{API}/suggestions", json={"content": "hi"})
    assert r.status_code == 401


def test_submit_persists(client):
    r = client.post(
        f"{API}/suggestions",
        json={"type": "bug", "content": "页面在 Safari 下白屏", "contact": "fb-user@test.local"},
        headers=_user_h(),
    )
    assert r.status_code == 201 and r.json()["ok"] is True
    db = next(app.dependency_overrides[get_db]())
    s = db.scalars(select(Suggestion)).first()
    assert s is not None
    assert s.type == "bug" and s.content == "页面在 Safari 下白屏"
    assert s.contact == "fb-user@test.local" and s.user_id == USER_ID


def test_submit_blank_content_422(client):
    r = client.post(f"{API}/suggestions", json={"content": ""}, headers=_user_h())
    assert r.status_code == 422


def test_list_forbidden_for_user(client):
    r = client.get(f"{API}/suggestions", headers=_user_h())
    assert r.status_code == 403


def test_list_admin_sees_submission(client):
    client.post(
        f"{API}/suggestions",
        json={"type": "suggestion", "content": "希望支持深色模式"},
        headers=_user_h(),
    )
    r = client.get(f"{API}/suggestions", headers=_admin_h())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["content"] == "希望支持深色模式"
    assert item["user_account"] == "fb-user@test.local"
    assert item["type"] == "suggestion"
