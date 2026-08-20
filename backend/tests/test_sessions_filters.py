"""Sessions 列表服务端过滤/分页测试（第三批 #7）：keyword / satisfaction / order / 翻页。

此前审计页一次拉 size=100 客户端过滤（第 101 条会话静默不可见）——
服务端补齐过滤参数 + 真分页，total 为过滤后真实总数。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
U1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
U2 = uuid.UUID("22222222-2222-2222-2222-222222222222")
U3 = uuid.UUID("33333333-3333-3333-3333-333333333333")

SATISFIED = uuid.UUID("44444444-4444-4444-4444-444444444444")
NEUTRAL = uuid.UUID("55555555-5555-5555-5555-555555555555")
NO_RATING = uuid.UUID("66666666-6666-6666-6666-666666666666")


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # messages.meta 是 JSONB（SQLite 无法编译）→ 建表前替换为 JSON（项目测试惯例）
    import sqlalchemy as sa

    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(engine, tables=[User.__table__, Session.__table__, Message.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    now = datetime.now()
    with Local() as db:
        db.add(User(id=ADMIN, email="admin@t.com", role=UserRole.admin, tenant_id="default", password_hash="x"))
        db.add(User(id=U1, email="alice@t.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(User(id=U2, email="bob@t.com", phone="13800000000", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(User(id=U3, email="carol@t.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        # 3 个会话：标题/满意度/创建时间各不同；updated_at 故意与 created_at 逆序（验证 order 参数区分度）
        db.add(Session(id=SATISFIED, user_id=U1, tenant_id="default", title="退货咨询",
                       satisfaction="satisfied", created_at=now - timedelta(days=3), updated_at=now))
        db.add(Session(id=NEUTRAL, user_id=U2, tenant_id="default", title="物流查询",
                       satisfaction="neutral", created_at=now - timedelta(days=2), updated_at=now - timedelta(days=1)))
        db.add(Session(id=NO_RATING, user_id=U3, tenant_id="default", title="保修政策",
                       created_at=now - timedelta(days=1), updated_at=now - timedelta(days=2)))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _admin_h():
    return {"Authorization": f"Bearer {create_access_token(str(ADMIN), 'admin')}"}


def _user_h(uid):
    return {"Authorization": f"Bearer {create_access_token(str(uid), 'user')}"}


def test_keyword_filters_title(client):
    r = client.get(f"{API}/sessions", params={"page": 1, "size": 20, "keyword": "退货"}, headers=_admin_h())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "退货咨询"


def test_keyword_filters_owner_email_and_phone(client):
    """admin 视角：keyword 命中客户邮箱/电话（BUG-12 字段），非仅标题。"""
    r1 = client.get(f"{API}/sessions", params={"keyword": "alice"}, headers=_admin_h())
    assert r1.json()["total"] == 1
    assert r1.json()["items"][0]["session_id"] == str(SATISFIED)

    r2 = client.get(f"{API}/sessions", params={"keyword": "13800000000"}, headers=_admin_h())
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["session_id"] == str(NEUTRAL)


def test_keyword_no_match(client):
    r = client.get(f"{API}/sessions", params={"keyword": "不存在的关键词"}, headers=_admin_h())
    assert r.json()["total"] == 0 and r.json()["items"] == []


def test_satisfaction_filter(client):
    r = client.get(f"{API}/sessions", params={"satisfaction": "neutral"}, headers=_admin_h())
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["session_id"] == str(NEUTRAL)
    # 未评分会话不进任何满意度筛选
    r2 = client.get(f"{API}/sessions", params={"satisfaction": "satisfied"}, headers=_admin_h())
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["session_id"] == str(SATISFIED)


def test_order_created_desc(client):
    """order=created → 按创建时间倒序（审计页语义；默认 updated 与之不同）。"""
    r = client.get(f"{API}/sessions", params={"order": "created"}, headers=_admin_h())
    ids = [i["session_id"] for i in r.json()["items"]]
    # created_at：保修(now-1d) > 物流(now-2d) > 退货(now-3d)
    assert ids == [str(NO_RATING), str(NEUTRAL), str(SATISFIED)]


def test_default_order_updated_unchanged(client):
    """默认排序仍按 updated_at desc（既有调用方兼容）。"""
    r = client.get(f"{API}/sessions", params={"page": 1, "size": 20}, headers=_admin_h())
    ids = [i["session_id"] for i in r.json()["items"]]
    # updated_at：退货(now) > 物流(now-1d) > 保修(now-2d)
    assert ids == [str(SATISFIED), str(NEUTRAL), str(NO_RATING)]


def test_pagination_page2(client):
    """size=2 造 3 条 → page=2 只剩 1 条；total 恒为 3（真实总数）。"""
    r = client.get(f"{API}/sessions", params={"page": 2, "size": 2, "order": "created"}, headers=_admin_h())
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 1
    # created 倒序下最后一页是最旧的"退货咨询"
    assert body["items"][0]["session_id"] == str(SATISFIED)


def test_user_view_keyword_still_scoped(client):
    """user 视角：keyword 只搜自己的会话（不因过滤参数放宽数据范围）。"""
    r = client.get(f"{API}/sessions", params={"keyword": "退货"}, headers=_user_h(U1))
    assert r.json()["total"] == 1  # 自己的
    r2 = client.get(f"{API}/sessions", params={"keyword": "退货"}, headers=_user_h(U2))
    assert r2.json()["total"] == 0  # 别人的不可见


def test_combined_filters(client):
    """keyword + satisfaction 组合：交集语义。"""
    r = client.get(
        f"{API}/sessions",
        params={"keyword": "alice", "satisfaction": "neutral"},
        headers=_admin_h(),
    )
    assert r.json()["total"] == 0  # alice 的会话是 satisfied，交集为空


# ---------- get_session 消息上限（第三批 #8：超长会话防全量加载） ----------

LONG_SID = uuid.UUID("77777777-7777-7777-7777-777777777777")


@pytest.fixture
def long_client():
    """专造 205 条消息的超长会话（LIMIT 语义：默认最新 200，limit 参数可调）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import sqlalchemy as sa

    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(engine, tables=[User.__table__, Session.__table__, Message.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    base = datetime.now() - timedelta(hours=10)
    with Local() as db:
        db.add(User(id=U1, email="long@t.com", role=UserRole.user, tenant_id="default", password_hash="x"))
        db.add(Session(id=LONG_SID, user_id=U1, tenant_id="default"))
        for i in range(205):
            db.add(Message(
                id=uuid.uuid4(), session_id=LONG_SID, tenant_id="default",
                role=MessageRole.user if i % 2 == 0 else MessageRole.assistant,
                content=f"msg-{i:03d}", created_at=base + timedelta(seconds=i),
            ))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_get_session_message_limit_default_200(long_client):
    """默认上限 200 条：返回最新 200（含最后一条），升序时间线。"""
    r = long_client.get(f"{API}/sessions/{LONG_SID}", headers=_user_h(U1))
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert len(msgs) == 200
    assert msgs[0]["content"] == "msg-005"  # 丢弃最旧 5 条
    assert msgs[-1]["content"] == "msg-204"  # 最新一条必须在
    # 时间线仍为升序（聊天历史语义）
    assert [m["content"] for m in msgs] == sorted([m["content"] for m in msgs])


def test_get_session_message_limit_param(long_client):
    """limit 参数显式可调（审计页拉全量上下文用大值）。"""
    r = long_client.get(f"{API}/sessions/{LONG_SID}", params={"limit": 1000}, headers=_user_h(U1))
    assert len(r.json()["messages"]) == 205


def test_get_session_message_limit_small(long_client):
    """limit=2：只回最新 2 条（轮询场景也依赖"最新优先"语义）。"""
    r = long_client.get(f"{API}/sessions/{LONG_SID}", params={"limit": 2}, headers=_user_h(U1))
    msgs = r.json()["messages"]
    assert [m["content"] for m in msgs] == ["msg-203", "msg-204"]
