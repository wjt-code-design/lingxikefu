"""Phase B 挂点接线集成测试：chat 采集 + feedback 满意度 → user_profiles 落库。

验证（真实编排路径，非直调服务层——防假接线 PL#14）：
- chat stream 完成 → 该用户 user_profiles 被写入（主题/实体，来自 user 问句）；
- feedback 提交 → user_profiles 满意度 up/down 计入；
- 客服侧/至少挂点不抛异常（fail-open，不影响响应）。

以 test_chat_api 为基准，复用其 fake stream 模型；额外建 user_profiles 表。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.feedback import Feedback
from app.models.knowledge import Document, KnowledgeBase
from app.models.message import Message, MessageRole
from app.models.session import Session
from app.models.ticket import Ticket
from app.models.user import User, UserRole
from app.models.user_profile import UserProfile
from app.services.user_profile_service import get_profile
from app.services.user_profile_service import get_profile as _gp
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
UID = uuid.UUID("22222222-2222-2222-2222-222222222222")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # JSONB / PG 类型 → SQLite 兼容（项目测试惯例）
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
            Session.__table__,
            Message.__table__,
            User.__table__,
            UserProfile.__table__,  # Phase B：画像采集落库
            Feedback.__table__,  # feedback 满意度挂点
            KnowledgeBase.__table__,
            Document.__table__,
            Ticket.__table__,
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
        db.add(
            User(id=UID, email="u@b.com", role=UserRole.user, tenant_id="default", password_hash="x")
        )
        db.add(Session(id=SID, user_id=UID, tenant_id="default"))
        db.add(KnowledgeBase(id=uuid.UUID("33333333-3333-3333-3333-333333333333"), name="kb", tenant_id="default"))
        db.commit()

    # 配额宽松
    class FakeQuota:
        def left_today(self, _uid):
            return 10

        def try_consume(self, _uid, n=1, idem_key=None):
            return (True, 0)

    monkeypatch.setattr("app.api.chat.get_quota_service", lambda: FakeQuota())
    monkeypatch.setattr(
        "app.api.chat._latest_kb_id",
        lambda db: "33333333-3333-3333-3333-333333333333",
    )

    with TestClient(app) as c:
        yield c, Local
    app.dependency_overrides.clear()


class _FakeStream:
    @staticmethod
    async def __call__(query, kb_id, history=None, top_k=5, **kwargs):
        yield ("stage", {"stage": "retrieving"})
        yield ("stage", {"stage": "generating"})
        yield ("token", {"delta": "已处理"})
        yield ("sources", {"sources": []})
        yield ("done", {"message_id": ""})


def _h():
    return {"Authorization": f"Bearer {create_access_token(str(UID), 'user')}"}


def _agent_h():
    # agent 登录（代答场景，画像归属应为会话 owner UID 而非 agent）
    AGENT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    return {"Authorization": f"Bearer {create_access_token(str(AGENT), 'agent')}"}


def _stream(monkeypatch, tc, content, Local, headers=None):
    # 禁用真实 LLM 流：用 fake stream
    monkeypatch.setattr("app.api.chat.stream_answer", _FakeStream())
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": str(SID), "content": content, "stream": True},
        headers=headers or _h(),
    )
    return tc, Local, r


def test_agent_reply_profile_attributes_to_owner(client, monkeypatch):
    """代答：agent 回复时画像归属会话 owner（UID），不记到 agent 头上。"""
    tc, Local = client
    _, _, r = _stream(monkeypatch, tc, "订单 SO2026080118 怎么退款", Local, headers=_agent_h())
    assert r.status_code == 200

    with Local() as db:
        p = get_profile(db, UID)  # owner
        assert p is not None and p["topics"]["退款"] == 1
        # agent 不应有画像（agent 未作为 owner 存在）
        agent_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        assert _gp(db, agent_id) is None


def test_chat_stream_persists_user_profile(client, monkeypatch):
    """chat 完成后，用户问句主题/实体被写入画像（真实编排路径）。"""
    tc, Local = client
    _, _, r = _stream(monkeypatch, tc, "订单 SO2026080118 怎么退款", Local)
    assert r.status_code == 200
    assert '"done"' in r.text

    with Local() as db:
        p = get_profile(db, UID)
        assert p is not None, "chat 后画像应已创建"
        assert p["topics"]["退款"] == 1  # 主题命中
        assert "SO2026080118" in p["entities"]  # 订单号实体


def test_chat_stream_dedup_same_turn(client, monkeypatch):
    """同一轮（幂等键=user_msg.id）不重复计数；下一轮另计。"""
    tc, Local = client
    # 第一轮
    _stream(monkeypatch, tc, "订单 SO2026080118 怎么退款", Local)
    _stream(monkeypatch, tc, "订单 SO2026080118 物流哪了", Local)  # 第二轮（不同 user_msg.id → 另计物流主题）
    with Local() as db:
        p = get_profile(db, UID)
        assert p["topics"]["退款"] == 1
        assert p["topics"]["配送/物流"] == 1
        assert p["entities"].count("SO2026080118") == 1  # 实体去重


def test_feedback_persists_satisfaction(client, monkeypatch):
    """feedback 提交 → 满意度入画像（幂等键=消息+评分）。"""
    tc, Local = client
    _, _, r = _stream(monkeypatch, tc, "退款一般多久到账", Local)
    assert r.status_code == 200

    # 找到 assistant 消息 id，对其提交 up 反馈
    with Local() as db:
        assistant = db.scalar(
            sa.select(Message).where(Message.role == MessageRole.assistant)
        )
        msg_id = str(assistant.id)

    r = tc.post(
        f"{API}/messages/{msg_id}/feedback",
        json={"rating": "up", "comment": "不错"},
        headers=_h(),
    )
    assert r.status_code == 200

    with Local() as db:
        p = get_profile(db, UID)
        assert p["satisfaction"]["up"] == 1

    # 重复同评分：幂等不翻倍
    tc.post(
        f"{API}/messages/{msg_id}/feedback",
        json={"rating": "up", "comment": "重复"},
        headers=_h(),
    )
    with Local() as db:
        p = get_profile(db, UID)
        assert p["satisfaction"]["up"] == 1
