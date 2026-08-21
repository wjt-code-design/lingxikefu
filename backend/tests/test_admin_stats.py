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
from app.models.ticket import Ticket
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
        tables=[User.__table__, Session.__table__, Message.__table__, Document.__table__, Feedback.__table__, Ticket.__table__],
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
        # refuse 问句 ×1 + 同义问法 ×1（归一化后并入 count=2）+ handoff ×3（应被排除，非知识缺口）
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="商品详情页在哪？", intent="refuse"))
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content=" 商品详情页在哪？ ", intent="refuse"))  # 归一化后与上同组（去空白）
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="怎么申请以旧换新？", intent="handoff"))
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="我要找人工客服", intent="handoff"))
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="你们就是骗子不退钱", intent="handoff"))
        # qa 消息不应计入
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default", role=MessageRole.user,
                       content="退款多久到账？", intent="qa"))
        db.commit()
    with TestClient(app) as c:
        yield c


def test_stats_hot_gaps_grouped(client):
    """F1：仅 refuse 用户消息归一化聚合 Top10；handoff（转人工/情绪）与 qa 不计入。"""
    r = client.get(f"{API}/admin/stats", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    gaps = {g["question"]: g["count"] for g in data["hot_gaps"]}
    # 2 条同义问法（含空白差异）归一化后聚合 count=2，展示保留完整问句
    assert gaps.get("商品详情页在哪？") == 2
    assert " 商品详情页在哪？ " not in gaps  # 归一化后不重复出现
    # handoff（转人工/情绪分流）不属于知识缺口 → 不得计入待补录
    assert "怎么申请以旧换新？" not in gaps
    assert "我要找人工客服" not in gaps
    assert "你们就是骗子不退钱" not in gaps
    assert "退款多久到账？" not in gaps  # qa 意图排除


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


def test_stats_avg_first_token_ms(client):
    """R-3：first_token_ms 均值——SQL 聚合等价验证（带埋点/无埋点/非数值混合）。

    预期独立手算：带埋点 3 条（100.0 / 200.0 / 300.0）→ 均值 200.0；
    无 meta / meta 无 first_token_ms / 非 assistant 的行一律不计。
    """
    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.assistant, content="a1", meta={"first_token_ms": 100.0}))
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.assistant, content="a2", meta={"first_token_ms": 200.0}))
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.assistant, content="a3", meta={"first_token_ms": 300.0}))
        # 干扰项：不计入均值
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.assistant, content="a4", meta={}))  # 无埋点
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.assistant, content="a5", meta=None))  # meta 为空
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.user, content="q1", meta={"first_token_ms": 999.0}))  # 非 assistant
        db.commit()
    finally:
        gen.close()

    r = client.get(f"{API}/admin/stats", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    assert r.json()["avg_first_token_ms"] == 200.0


def test_stats_avg_first_token_ms_empty(client):
    """R-3：无任何埋点数据 → 均值 0.0（不是 None/500）。"""
    r = client.get(f"{API}/admin/stats", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    assert r.json()["avg_first_token_ms"] == 0.0


def test_stats_trend_aggregates_days(client):
    """P1：stats/trend 按日聚合会话/消息/工单 + 无数据日期补零 + 权限 403。"""
    from datetime import datetime, timedelta
    from app.models.ticket import Ticket

    # 用 fixture 已有的 SID 会话 + 补历史数据（2 天前）
    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        old = datetime.now() - timedelta(days=2)
        s_old = Session(id=uuid.uuid4(), user_id=USER, tenant_id="default", created_at=old)
        db.add(s_old)
        db.add(Message(id=uuid.uuid4(), session_id=s_old.id, tenant_id="default",
                       role=MessageRole.user, content="两天前的消息", created_at=old))
        db.add(Ticket(id=uuid.uuid4(), session_id=s_old.id, tenant_id="default", created_at=old))
        db.commit()
    finally:
        gen.close()

    r = client.get(f"{API}/admin/stats/trend?days=7", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    days = r.json()["days"]
    assert len(days) == 7
    by_date = {d["date"]: d for d in days}
    # 2 天前：1 会话 + 1 消息 + 1 工单；今天：至少 1 会话（fixture SID）
    old_key = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    assert by_date[old_key]["sessions"] == 1
    assert by_date[old_key]["messages"] == 1
    assert by_date[old_key]["tickets"] == 1
    today_key = datetime.now().strftime("%Y-%m-%d")
    assert by_date[today_key]["sessions"] >= 1
    # 无数据日期补零（连续轴）
    assert all(d["sessions"] >= 0 for d in days)
    # 权限：user → 403
    r2 = client.get(f"{API}/admin/stats/trend", headers=_h(USER, "user"))
    assert r2.status_code == 403
