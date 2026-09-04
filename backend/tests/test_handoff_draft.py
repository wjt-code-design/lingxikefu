"""低风险 handoff 判别 + 建单 AI 预起草 + TicketItem 下发补全（架构二期 1，L2 预起草）。

覆盖：
- classify_handoff_risk：显式人工/投诉/情绪 → high（硬约束：显式转人工不可改道）；
  知识型（conv_state.topic ∈ FLOW_TOPICS 且非显式词）→ low；无主题 → high；
  现网 handoff/情绪词表全表回归（全部 high——防词表扩充后绕过白名单）；
- 建单预起草：TicketAgent low risk fire-and-forget 调度 / high risk 不调度 /
  调度失败 fail-open 降级留痕 / 真实线程池投递；后台 worker
  （ticket_service.draft_ticket_suggestion）写 draft_suggestion + draft_kind="ai"、
  首草为准不覆盖、LLM 失败草稿留空（fail-open，不影响建单）；
- chat 接线：handoff_risk 进 ctx（low → 调度预起草；显式转人工 → 建单但绝不预起草）；
- TicketItem 下发补全（一期 T3 遗留）：summary / draft_suggestion / draft_kind /
  processing_at / resolved_at 此前只写不下发。

手法沿用既有测试（test_chat_api / test_agents/test_ticket_agent / test_sessions_suggest）：
SQLite in-memory + StaticPool + Base.metadata.create_all 显式建表，不依赖真实 PG/Qdrant/LLM。
"""
from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Document, KnowledgeBase
from app.models.message import Message
from app.models.notification import Notification
from app.models.session import Session as SessionModel
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User
from app.services.retrieval_service import RetrievedChunk
from app.services.session_context import classify_handoff_risk
from app.services.shared_context import SharedContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
AGENT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")
KB_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
DOC_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


# ── 共享基建 ─────────────────────────────────────────────────────────


def _sqlite(tables) -> sessionmaker:
    """SQLite in-memory + 显式建表（messages.meta JSONB → SQLite 需替换类型）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(engine, tables=tables)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1", doc_id=str(DOC_ID), kb_id=str(KB_ID), idx=0,
        text="电子发票可在订单页自助开具，支持个人电子普票与企业专票", score=0.9, dense_score=0.9,
    )


class _FakeChatClient:
    """非流式 complete 替身：计数 + 可注入异常（同 test_sessions_suggest 手法）。"""

    def __init__(self, text: str = "您好，电子发票可在订单页自助开具 [来源1]。", error: Exception | None = None):
        self._text, self._error, self.calls = text, error, 0
        self.last_messages: list[dict] | None = None

    async def complete(self, messages, **kwargs) -> str:
        self.calls += 1
        self.last_messages = messages
        if self._error:
            raise self._error
        return self._text


def _patch_assist_defaults(monkeypatch, text: str = "您好，电子发票可在订单页自助开具 [来源1]。", error: Exception | None = None):
    """打后台预起草 worker 走的 agent_assist 默认依赖（KB 定位/检索/LLM）。"""
    monkeypatch.setattr("app.services.agent_assist._default_kb_lookup", lambda db: KB_ID)
    monkeypatch.setattr("app.services.agent_assist._default_search_kb", lambda q, kb_id, top_k=3: [_chunk()])
    fake = _FakeChatClient(text=text, error=error)
    monkeypatch.setattr("app.services.agent_assist._default_chat_client", lambda: fake)
    return fake


# ── 1. classify_handoff_risk：低风险判别（纯函数） ────────────────────


def test_explicit_human_always_high():
    """硬约束：显式转人工（含裸「人工」正则/真人客服/找人工/经理）命中即 high，有主题也不得 low。"""
    for q in ("转人工", "请转人工客服", "人工服务窗口在哪", "我要找人工", "接个真人客服", "给我转经理", "你好，人工"):
        assert classify_handoff_risk(q, {"topic": "发票"}) == "high", q


def test_complaint_and_emotion_high():
    """投诉/情绪词 → high（有知识型主题也压不住）。"""
    assert classify_handoff_risk("我要投诉", {"topic": "退款"}) == "high"
    assert classify_handoff_risk("气死我了", {"topic": "退款"}) == "high"
    assert classify_handoff_risk("退钱！骗子", {"topic": "退款"}) == "high"


def test_knowledge_topic_low():
    """知识型：非显式词 + conv_state.topic ∈ FLOW_TOPICS → low（可 AI 预起草改道）。"""
    assert classify_handoff_risk("怎么开发票", {"stage": "resolving", "topic": "发票", "slots": {}, "clarify_count": 0}) == "low"
    assert classify_handoff_risk("退款多久到账", {"topic": "退款"}) == "low"
    assert classify_handoff_risk("运费谁出", {"topic": "配送/物流"}) == "low"


def test_no_topic_high():
    """无主题（conv_state 缺省/空）→ high（保守不预起草）。"""
    assert classify_handoff_risk("帮我看看", None) == "high"
    assert classify_handoff_risk("帮我看看", {}) == "high"
    assert classify_handoff_risk("帮我看看", {"topic": ""}) == "high"


def test_emotion_context_exclusion_same_as_intent():
    """情绪语境排除与 classify_intent 同口径：垃圾袋（商品语境）不判情绪 → 有主题即 low。"""
    assert classify_handoff_risk("垃圾袋多少钱一个", {"topic": "退换货"}) == "low"


def test_all_current_handoff_triggers_high():
    """现网 rag_service 词表全表回归：handoff/情绪词逐词判 high——词表日后扩充时
    本测试强制新词落入高风险（白名单只能显式放行知识型，防改道拦下明确人工诉求）。"""
    from app.services.rag_service import EMOTIONAL_KEYWORDS, HANDOFF_KEYWORDS

    state = {"topic": "发票"}
    for k in HANDOFF_KEYWORDS:
        assert classify_handoff_risk(k, state) == "high", k
    for k in EMOTIONAL_KEYWORDS:
        assert classify_handoff_risk(k, state) == "high", k


# ── 2. 建单 AI 预起草：TicketAgent 调度（fire-and-forget） ────────────


async def test_agent_low_risk_schedules_draft(monkeypatch):
    """low risk 建单成功 → 后台预起草被调度（工单 id + 触发问句）。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__])
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.agents.ticket_agent._schedule_draft",
        lambda tid, q, trace="": captured.append((tid, q)),
    )
    from app.services.agents.ticket_agent import TicketAgent

    with Local() as db:
        ctx = SharedContext(
            query="怎么开发票", intent="handoff", session_id=uuid.uuid4(),
            db=db, handoff_risk="low", trace_id="t-low",
        )
        ctx = await TicketAgent().run(ctx)
        assert ctx.ticket_id is not None
        assert captured == [(ctx.ticket_id, "怎么开发票")]


async def test_agent_high_risk_no_draft(monkeypatch):
    """high risk（显式转人工）→ 建单但绝不预起草。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__])
    captured: list[tuple] = []
    monkeypatch.setattr(
        "app.services.agents.ticket_agent._schedule_draft",
        lambda tid, q, trace="": captured.append((tid, q)),
    )
    from app.services.agents.ticket_agent import TicketAgent

    with Local() as db:
        ctx = SharedContext(query="转人工", intent="handoff", session_id=uuid.uuid4(), db=db, handoff_risk="high")
        ctx = await TicketAgent().run(ctx)
        assert ctx.ticket_id is not None  # 建单照常
        assert captured == []


async def test_agent_unclassified_risk_defaults_to_no_draft(monkeypatch):
    """handoff_risk 未填（""）→ 保守不预起草（只认显式 low）。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__])
    captured: list[tuple] = []
    monkeypatch.setattr(
        "app.services.agents.ticket_agent._schedule_draft",
        lambda tid, q, trace="": captured.append((tid, q)),
    )
    from app.services.agents.ticket_agent import TicketAgent

    with Local() as db:
        ctx = SharedContext(query="投诉", intent="handoff", session_id=uuid.uuid4(), db=db)
        ctx = await TicketAgent().run(ctx)
        assert ctx.ticket_id is not None
        assert captured == []


async def test_agent_draft_schedule_failure_fail_open(monkeypatch):
    """调度失败 → 不抛出、建单结果保持、降级留痕（draft_suggestion 留空由坐席侧感知）。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__])

    def _boom(*_a, **_k):
        raise RuntimeError("pool down")

    monkeypatch.setattr("app.services.agents.ticket_agent._schedule_draft", _boom)
    from app.services.agents.ticket_agent import TicketAgent

    with Local() as db:
        ctx = SharedContext(query="怎么开发票", intent="handoff", session_id=uuid.uuid4(), db=db, handoff_risk="low")
        ctx = await TicketAgent().run(ctx)  # 不得抛出
        assert ctx.ticket_id is not None  # 建单结果不受影响
        assert "ticket:draft_schedule_failed" in ctx.degraded


async def test_agent_real_pool_delivers_to_worker(monkeypatch):
    """真实线程池投递（M4 教验验证）：submit 后 worker 在后台线程被调起。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__])
    done = threading.Event()
    seen: dict[str, str] = {}

    def _fake_worker(tid, q, trace=""):
        seen.update(tid=tid, q=q)
        done.set()

    monkeypatch.setattr("app.services.agents.ticket_agent.draft_ticket_suggestion", _fake_worker)
    from app.services.agents.ticket_agent import TicketAgent

    with Local() as db:
        ctx = SharedContext(query="怎么开发票", intent="handoff", session_id=uuid.uuid4(), db=db, handoff_risk="low")
        ctx = await TicketAgent().run(ctx)
        assert ctx.ticket_id is not None
        assert done.wait(timeout=10), "预起草 worker 未被线程池调起"
        assert seen["tid"] == ctx.ticket_id and seen["q"] == "怎么开发票"


# ── 3. 后台 worker：draft_ticket_suggestion（同步，独立会话） ─────────


def test_draft_worker_writes_ai_draft(monkeypatch):
    """low risk 建单后起草：draft_suggestion 落 LLM 草稿 + draft_kind="ai"。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__, SessionModel.__table__, Message.__table__])
    sid = uuid.uuid4()
    with Local() as db:
        db.add(SessionModel(id=sid, user_id=USER_ID))
        db.commit()
        t = Ticket(tenant_id="default", session_id=sid)
        db.add(t)
        db.commit()
        tid = str(t.id)

    fake = _patch_assist_defaults(monkeypatch)
    from app.services.ticket_service import draft_ticket_suggestion

    draft_ticket_suggestion(tid, "怎么开发票", trace_id="t-w1", session_factory=Local)

    with Local() as db:
        row = db.get(Ticket, uuid.UUID(tid))
        assert row is not None
        assert row.draft_suggestion == "您好，电子发票可在订单页自助开具 [来源1]。"
        assert row.draft_kind == "ai"
    assert fake.calls == 1
    # 草稿对象是触发问句（assist prompt 的「顾客最新消息」块）
    assert fake.last_messages is not None and "怎么开发票" in fake.last_messages[-1]["content"]


def test_draft_worker_first_draft_wins(monkeypatch):
    """已有草稿（首草为准，对齐 summary 幂等语义）→ 跳过，不覆盖、不打 LLM。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__, SessionModel.__table__, Message.__table__])
    sid = uuid.uuid4()
    with Local() as db:
        db.add(SessionModel(id=sid, user_id=USER_ID))
        t = Ticket(tenant_id="default", session_id=sid, draft_suggestion="人工已编辑草稿", draft_kind="agent")
        db.add(t)
        db.commit()
        tid = str(t.id)

    fake = _patch_assist_defaults(monkeypatch)
    from app.services.ticket_service import draft_ticket_suggestion

    draft_ticket_suggestion(tid, "怎么开发票", session_factory=Local)

    with Local() as db:
        row = db.get(Ticket, uuid.UUID(tid))
        assert row.draft_suggestion == "人工已编辑草稿"
        assert row.draft_kind == "agent"
    assert fake.calls == 0


def test_draft_worker_llm_failure_fail_open(monkeypatch):
    """LLM 失败 → fail-open：草稿留空（NULL）、不抛出（不影响建单/问答）。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__, SessionModel.__table__, Message.__table__])
    sid = uuid.uuid4()
    with Local() as db:
        db.add(SessionModel(id=sid, user_id=USER_ID))
        t = Ticket(tenant_id="default", session_id=sid)
        db.add(t)
        db.commit()
        tid = str(t.id)

    _patch_assist_defaults(monkeypatch, error=RuntimeError("llm down"))
    from app.services.ticket_service import draft_ticket_suggestion

    draft_ticket_suggestion(tid, "怎么开发票", session_factory=Local)  # 不得抛出

    with Local() as db:
        row = db.get(Ticket, uuid.UUID(tid))
        assert row.draft_suggestion is None
        assert row.draft_kind is None


def test_draft_worker_missing_ticket_noop(monkeypatch):
    """工单不存在（竞态删除等）→ 静默 no-op，不抛出。"""
    Local = _sqlite([Ticket.__table__, Notification.__table__, SessionModel.__table__, Message.__table__])
    _patch_assist_defaults(monkeypatch)
    from app.services.ticket_service import draft_ticket_suggestion

    draft_ticket_suggestion(str(uuid.uuid4()), "怎么开发票", session_factory=Local)


# ── 4. chat 接线：handoff_risk 进 ctx → 调度 ─────────────────────────


@pytest.fixture
def chat_client(monkeypatch):
    """/chat/stream 最小环境（同 test_chat_api 手法：mock quota/kb，真实 conv_state）。"""
    Local = _sqlite(
        [
            SessionModel.__table__, Message.__table__,
            KnowledgeBase.__table__, Document.__table__,
            Ticket.__table__, User.__table__,
        ]
    )

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(SessionModel(id=SID, user_id=USER_ID))
        db.add(KnowledgeBase(id=KB_ID, name="星河测试库"))
        db.commit()

    class FakeQuota:
        def left_today(self, _uid):
            return 10

        def try_consume(self, _uid, n=1, idem_key=None, content=None, token=None, guest=False):
            return (True, 0)

        def refund(self, _uid, n=1, idem_key=None, content=None, token=None):
            return 0

    monkeypatch.setattr("app.api.chat.get_quota_service", lambda: FakeQuota())
    monkeypatch.setattr("app.api.chat._latest_kb_id", lambda db: KB_ID)

    with TestClient(app) as c:
        yield c, Local
    app.dependency_overrides.clear()


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def _handoff_stream():
    async def _handoff(*_a, **_k):
        yield ("intent", {"intent": "handoff"})
        yield ("token", {"delta": "已为您转接人工"})
        yield ("done", {"message_id": ""})

    return _handoff


def test_chat_low_risk_handoff_schedules_draft(chat_client, monkeypatch):
    """全链路：知识型问句（低风险判别注入 chat）→ handoff 建单 → 调度预起草。

    注：现网 Router 对「怎么开发票」判 qa 不排入 TICKET_AGENT（L2 改道属后续任务），
    此处 patch Router 前置分类模拟改道后的 Router 状态——本测试验证的是 chat 层
    handoff_risk 接线与调度，判别函数本体由上方单测覆盖（显式人工链路用真实判别：
    test_chat_explicit_human_no_draft）。"""
    tc, Local = chat_client
    scheduled: list[tuple[str, str]] = []
    risk_seen: list[tuple[str, dict | None]] = []
    monkeypatch.setattr("app.services.agents.router.classify_intent", lambda q: "handoff")
    monkeypatch.setattr(
        "app.api.chat.classify_handoff_risk",
        lambda q, s: (risk_seen.append((q, s)), "low")[1],
    )
    monkeypatch.setattr(
        "app.services.agents.ticket_agent._schedule_draft",
        lambda tid, q, trace="": scheduled.append((tid, q)),
    )
    monkeypatch.setattr("app.api.chat.stream_answer", _handoff_stream())

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": str(SID), "content": "怎么开发票", "stream": True},
        headers=_user_h(),
    )
    assert r.status_code == 200

    # 判别入参 = 触发问句 + 行锁读改写后的 conv_state（「发票」主题已由状态机判出）
    assert risk_seen and risk_seen[0][0] == "怎么开发票"
    assert risk_seen[0][1] is not None and risk_seen[0][1].get("topic") == "发票"
    with Local() as db:
        t = db.scalar(select(Ticket))
        assert t is not None and t.status == TicketStatus.open
        assert scheduled == [(str(t.id), "怎么开发票")]


def test_chat_explicit_human_no_draft(chat_client, monkeypatch):
    """硬约束全链路：显式「转人工」（真实判别=high）→ 建单但绝不预起草。"""
    tc, Local = chat_client
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        "app.services.agents.ticket_agent._schedule_draft",
        lambda tid, q, trace="": scheduled.append((tid, q)),
    )
    monkeypatch.setattr("app.api.chat.stream_answer", _handoff_stream())

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": str(SID), "content": "转人工", "stream": True},
        headers=_user_h(),
    )
    assert r.status_code == 200
    with Local() as db:
        assert db.scalar(select(Ticket)) is not None  # 建单照常
    assert scheduled == []  # 显式人工不可改道：无预起草


# ── 5. TicketItem 下发补全（一期 T3 遗留） ───────────────────────────


@pytest.fixture
def tickets_client():
    Local = _sqlite([SessionModel.__table__, Ticket.__table__])

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(SessionModel(id=SID, user_id=USER_ID))
        db.commit()
    with TestClient(app) as c:
        yield c, Local
    app.dependency_overrides.clear()


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT_ID), 'agent')}"}


def test_ticket_item_emits_summary_draft_and_timestamps(tickets_client):
    """下发补全：summary（T3 遗留只写不发）+ draft_suggestion/draft_kind + 流转时间戳。"""
    tc, Local = tickets_client
    now = datetime.now(UTC)
    with Local() as db:
        t = Ticket(
            tenant_id="default",
            session_id=SID,
            summary='{"topic": "发票", "question": "怎么开发票"}',
            draft_suggestion="您好，电子发票可在订单页自助开具。",
            draft_kind="ai",
            processing_at=now,
            resolved_at=now,
        )
        db.add(t)
        db.commit()
        tid = str(t.id)

    r = tc.get(f"{API}/tickets/{tid}", headers=_agent_h())
    assert r.status_code == 200
    item = r.json()
    assert item["summary"] == '{"topic": "发票", "question": "怎么开发票"}'
    assert item["draft_suggestion"] == "您好，电子发票可在订单页自助开具。"
    assert item["draft_kind"] == "ai"
    assert item["processing_at"] is not None and item["resolved_at"] is not None

    # 列表同构下发
    r2 = tc.get(f"{API}/tickets", headers=_agent_h())
    assert r2.status_code == 200 and r2.json()["total"] == 1
    item2 = r2.json()["items"][0]
    assert item2["draft_suggestion"] == "您好，电子发票可在订单页自助开具。"
    assert item2["summary"] == '{"topic": "发票", "question": "怎么开发票"}'


def test_ticket_item_defaults_null(tickets_client):
    """未起草/未流转工单：新字段 NULL 下发（不破坏既有消费方）。"""
    tc, _ = tickets_client
    r = tc.post(f"{API}/tickets", json={"session_id": str(SID)}, headers=_agent_h())
    assert r.status_code == 201
    item = r.json()
    assert item["summary"] is None
    assert item["draft_suggestion"] is None
    assert item["draft_kind"] is None
    assert item["processing_at"] is None
    assert item["resolved_at"] is None
