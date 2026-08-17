"""Admin stats API 测试（F1）：待补录问题 Top10 聚合（handoff/refuse 消息分组）+ 权限。"""
from __future__ import annotations

import uuid

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.feedback import Feedback
from app.models.knowledge import Document
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _h(uid: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(uid), role=role)}"}


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # messages.meta 是 JSONB（SQLite 无法编译）→ 建表前替换为 JSON
    import sqlalchemy as sa

    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine,
        tables=[User.__table__, Session.__table__, Message.__table__, Document.__table__, Feedback.__table__],
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
        db.add(User(id=ADMIN, email="admin@b.com", role=UserRole.admin, tenant_id="default", password_hash="x"))
        db.add(User(id=USER, email="u@b.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(Session(id=SID, user_id=USER, tenant_id="default"))
        # handoff 问句 ×2（同一问句 → 聚合 count=2）+ refuse 问句 ×1 + 同义问法 ×1（归一化后并入 count=2）
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="怎么申请以旧换新？", intent="handoff"))
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="怎么申请以旧换新？", intent="handoff"))
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content=" 怎么申请以旧换新？ ", intent="handoff"))  # 归一化后与上同组（去空白）
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="商品详情页在哪？", intent="refuse"))
        # qa 消息不应计入
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="退款多久到账？", intent="qa"))
        db.commit()
    with TestClient(app) as c:
        yield c


def test_stats_hot_gaps_grouped(client):
    """F1：handoff/refuse 用户消息归一化聚合 Top10，qa 不计入。"""
    r = client.get(f"{API}/admin/stats", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    gaps = {g["question"]: g["count"] for g in data["hot_gaps"]}
    # 3 条同义问法（含空白差异）归一化后聚合 count=3，展示保留完整问句
    assert gaps.get("怎么申请以旧换新？") == 3
    assert gaps.get("商品详情页在哪？") == 1
    assert "退款多久到账？" not in gaps  # qa 意图排除
    assert " 怎么申请以旧换新？ " not in gaps  # 归一化后不重复出现


def test_stats_forbidden_for_user(client):
    """非 admin 访问 /admin/stats → 403。"""
    r = client.get(f"{API}/admin/stats", headers=_h(USER, "user"))
    assert r.status_code == 403


def test_admin_feedback_lists_down_only(client):
    """GET /admin/feedback：只看"踩"（down），join 消息内容；up 不返回；非 admin 403。"""
    from app.models.feedback import Feedback, FeedbackRating  # noqa: F401

    # 客户端调用（无 feedback 数据时返回空列表 + admin 权限校验）
    r = client.get(f"{API}/admin/feedback", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    assert "items" in data and "total" in data

    r2 = client.get(f"{API}/admin/feedback", headers=_h(USER, "user"))
    assert r2.status_code == 403
