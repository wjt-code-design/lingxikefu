"""坐席辅助端点测试（批次A）：POST /sessions/{id}/suggest 权限 / fail-open / 缓存 / 标题补全。

覆盖：
- 无凭证 → 401；user → 403；会话不存在 → 404；
- 正常 → 200，text 非空 + sources 带 doc_title（标题唯一真源）；
- 无用户消息且未传 question → 422；
- LLM 异常 → 200 空建议（fail-open，不 5xx）；
- 无知识库 → 200 空建议；
- 60s 结果缓存：同 (session, question) 二次调用 LLM 只打一次。
"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.api.sessions import _suggest_cache
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Document, KnowledgeBase
from app.models.message import Message, MessageSource
from app.models.session import Session
from app.models.user import User, UserRole
from app.services.retrieval_service import RetrievedChunk
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
AGENT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")
EMPTY_SID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
KB_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
DOC_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


class _FakeChatClient:
    """非流式 complete 替身：计数 + 可注入异常。"""

    def __init__(self, text: str = "您好，退款一般 1-3 个工作日原路退回 [来源1]。", error: Exception | None = None):
        self._text, self._error, self.calls = text, error, 0

    async def complete(self, messages, **kwargs) -> str:
        self.calls += 1
        if self._error:
            raise self._error
        return self._text


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1", doc_id=str(DOC_ID), kb_id=str(KB_ID), idx=0,
        text="退款 1-3 个工作日原路退回", score=0.9, dense_score=0.9,
    )


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine,
        tables=[
            Session.__table__, Message.__table__, User.__table__, MessageSource.__table__,
            KnowledgeBase.__table__, Document.__table__,
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
        db.add(User(id=AGENT_ID, role=UserRole.agent, email="agent@test.local", password_hash="x", status="active"))
        db.add(Session(id=SID, user_id=USER_ID))
        db.add(Session(id=EMPTY_SID, user_id=USER_ID))
        db.add(KnowledgeBase(id=KB_ID, name="售后库"))
        db.add(Document(id=DOC_ID, kb_id=KB_ID, name="退换货政策", sha256="x" * 64, status="indexed"))
        db.add(Message(session_id=SID, role="user", content="退款多久到账？", intent="qa"))
        db.commit()

    # 默认替身：检索命中 1 chunk、KB 存在、LLM 正常（各用例可覆盖）
    monkeypatch.setattr("app.api.sessions._latest_kb_id", lambda db: KB_ID)
    monkeypatch.setattr("app.api.sessions.search_kb", lambda q, kb_id, top_k=3: [_chunk()])
    fake = _FakeChatClient()
    monkeypatch.setattr("app.api.sessions.get_chat_client", lambda: fake)

    _suggest_cache.clear()
    with TestClient(app) as c:
        yield c
    _suggest_cache.clear()
    app.dependency_overrides.clear()


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT_ID), 'agent')}"}


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def test_suggest_requires_auth(client):
    assert client.post(f"{API}/sessions/{SID}/suggest", json={}).status_code == 401


def test_suggest_forbidden_for_user(client):
    assert client.post(f"{API}/sessions/{SID}/suggest", json={}, headers=_user_h()).status_code == 403


def test_suggest_not_found(client):
    r = client.post(f"{API}/sessions/{uuid.uuid4()}/suggest", json={}, headers=_agent_h())
    assert r.status_code == 404


def test_suggest_ok_with_default_question(client):
    """默认取最近一条顾客消息；text 来自 LLM；sources 补 doc_title（唯一真源）。"""
    r = client.post(f"{API}/sessions/{SID}/suggest", json={}, headers=_agent_h())
    assert r.status_code == 200
    data = r.json()
    assert "退款" in data["text"]
    assert data["sources"][0]["doc_title"] == "退换货政策"
    assert data["sources"][0]["snippet"].startswith("退款 1-3")


def test_suggest_explicit_question(client, monkeypatch):
    seen: list[str] = []

    def _fake_search(q, kb_id, top_k=3):
        seen.append(q)
        return [_chunk()]

    monkeypatch.setattr("app.api.sessions.search_kb", _fake_search)
    r = client.post(f"{API}/sessions/{SID}/suggest", json={"question": "发票怎么开"}, headers=_agent_h())
    assert r.status_code == 200
    assert seen == ["发票怎么开"]


def test_suggest_no_user_message_422(client):
    """空会话（无任何顾客消息）且未传 question → 422。"""
    r = client.post(f"{API}/sessions/{EMPTY_SID}/suggest", json={}, headers=_agent_h())
    assert r.status_code == 422


def test_suggest_llm_error_fail_open(client, monkeypatch):
    monkeypatch.setattr(
        "app.api.sessions.get_chat_client",
        lambda: _FakeChatClient(error=RuntimeError("llm down")),
    )
    r = client.post(f"{API}/sessions/{SID}/suggest", json={"question": "换个问题避免缓存"}, headers=_agent_h())
    assert r.status_code == 200
    assert r.json() == {"text": "", "sources": []}


def test_suggest_no_kb_fail_open(client, monkeypatch):
    monkeypatch.setattr("app.api.sessions._latest_kb_id", lambda db: None)
    r = client.post(f"{API}/sessions/{SID}/suggest", json={"question": "再换个问题"}, headers=_agent_h())
    assert r.status_code == 200
    assert r.json() == {"text": "", "sources": []}


def test_suggest_cache_hit(client, monkeypatch):
    fake = _FakeChatClient()
    monkeypatch.setattr("app.api.sessions.get_chat_client", lambda: fake)
    q = {"question": "缓存测试问题"}
    r1 = client.post(f"{API}/sessions/{SID}/suggest", json=q, headers=_agent_h())
    r2 = client.post(f"{API}/sessions/{SID}/suggest", json=q, headers=_agent_h())
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert fake.calls == 1  # 二次命中缓存，LLM 只调一次


def test_suggest_refresh_bypasses_cache(client, monkeypatch):
    """refresh=true 绕过结果缓存：同 (session, question) 二次调用各打一次 LLM（重新生成）。"""
    fake = _FakeChatClient()
    monkeypatch.setattr("app.api.sessions.get_chat_client", lambda: fake)
    q = {"question": "重新生成测试问题", "refresh": True}
    r1 = client.post(f"{API}/sessions/{SID}/suggest", json=q, headers=_agent_h())
    r2 = client.post(f"{API}/sessions/{SID}/suggest", json=q, headers=_agent_h())
    assert r1.status_code == r2.status_code == 200
    assert r2.json() == r1.json()  # 新结果仍写入缓存（fake 文本恒定）
    assert fake.calls == 2  # refresh 跳过缓存读取，两次都真调 LLM
