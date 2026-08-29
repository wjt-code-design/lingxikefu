"""Chat API 测试（BU-06）：SSE 事件顺序 / 会话归属 / 配额 / 来源落库。

- SQLite StaticPool + get_db 覆盖；建 session/message/message_sources/kb 表；
- mock stream_answer（直接 yield 契约事件序列），不依赖真实 RAG/Qdrant/百炼；
- mock quota（避免 Redis 依赖）。
"""
from __future__ import annotations

import uuid

import app.models.knowledge  # noqa: F401
import pytest
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Document, KnowledgeBase
from app.models.message import Message, MessageRole, MessageSource
from app.models.session import Session
from app.models.ticket import Ticket, TicketStatus
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # messages.meta 是 JSONB + PG server_default（SQLite 无法编译）→ 建表前替换
    import sqlalchemy as sa

    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(
        engine,
        tables=[
            Session.__table__,
            Message.__table__,
            MessageSource.__table__,
            KnowledgeBase.__table__,
            Document.__table__,
            Ticket.__table__,  # T1：handoff 建单测试
            User.__table__,  # BUG-12：list_sessions 回填 user_email/user_phone
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

    # 初始数据：session + kb（sa.Uuid 列需传 uuid.UUID 对象，SQLite 不接受字符串）
    import uuid as _uuid

    with Local() as db:
        db.add(Session(id=_uuid.UUID("11111111-1111-1111-1111-111111111111"), user_id=_uuid.UUID("22222222-2222-2222-2222-222222222222")))
        db.add(KnowledgeBase(id=_uuid.UUID("33333333-3333-3333-3333-333333333333"), name="星河测试库"))
        db.commit()

    # mock quota：余额充足，记录 try_consume 调用（M2 后消耗走原子闸门）
    calls = {"consumed": 0}

    class FakeQuota:
        def left_today(self, _uid):
            return 10

        def try_consume(self, _uid, n=1, idem_key=None):  # idem_key：与生产签名对齐（配额幂等键，2026-08-20 补）
            calls["consumed"] += n
            return (True, 0)

        def refund(self, _uid, n=1, idem_key=None):
            calls["consumed"] -= n
            return 0

    monkeypatch.setattr("app.api.chat.get_quota_service", lambda: FakeQuota())
    monkeypatch.setattr(
        "app.api.chat._latest_kb_id",
        lambda db: "33333333-3333-3333-3333-333333333333",
    )

    with TestClient(app) as c:
        yield c, Local, calls
    app.dependency_overrides.clear()


def _headers():
    return {"Authorization": f"Bearer {create_access_token('22222222-2222-2222-2222-222222222222', 'user')}"}


class _FakeStream:
    """模拟 stream_answer：按契约事件序列 yield（token → sources → done）。"""

    @staticmethod
    async def __call__(query, kb_id, history=None, top_k=5, **kwargs):  # **kwargs：兼容 T10 kb_version
        yield ("stage", {"stage": "retrieving"})
        yield ("stage", {"stage": "generating"})
        yield ("token", {"delta": "保修"})
        yield ("token", {"delta": "12个月"})
        yield (
            "sources",
            {
                "sources": [
                    {
                        "chunk_id": "44444444-4444-4444-4444-444444444444",
                        "doc_id": "55555555-5555-5555-5555-555555555555",
                        "score": 0.9,
                        "snippet": "保修期12个月",
                    }
                ]
            },
        )
        yield ("done", {"message_id": ""})


def test_chat_stream_events_and_persist(client, monkeypatch):
    tc, Local, calls = client
    monkeypatch.setattr("app.api.chat.stream_answer", _FakeStream())

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "退货运费谁出", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    body = r.text
    # 事件顺序：stage retrieving → stage generating → token*2 → sources → done
    events = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    assert len(events) == 6
    assert '"retrieving"' in events[0] and '"generating"' in events[1]
    assert '"保修"' in events[2] and '"12个月"' in events[3]
    assert '"sources"' in events[4]
    assert '"done"' in events[5]

    # 落库：user + assistant 两条消息，1 条 source，配额已扣
    with Local() as db:
        msgs = db.scalars(select(Message)).all()
        roles = sorted(m.role.value for m in msgs)
        assert roles == ["assistant", "user"]
        assistant = next(m for m in msgs if m.role == MessageRole.assistant)
        assert "保修" in assistant.content and "12个月" in assistant.content
        srcs = db.scalars(select(MessageSource)).all()
        assert len(srcs) == 1
        assert srcs[0].doc_id == __import__("uuid").UUID("55555555-5555-5555-5555-555555555555")
    assert calls["consumed"] == 1


def test_chat_stream_done_carries_trace_id(client, monkeypatch):
    """P0-1 trace_id：HTTP 层 request_id 贯通到 SSE done（业务链路可观测）。

    断言：响应头 X-Request-ID 与 done 事件 trace_id 同源——证明入口生成的
    request_id 已接入业务链路（此前只到错误模型，不进 SSE）。
    """
    tc, _, _ = client
    monkeypatch.setattr("app.api.chat.stream_answer", _FakeStream())

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "退货运费谁出", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    request_id = r.headers.get("X-Request-ID")
    assert request_id  # 中间件生成的请求 ID
    events = [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]
    done = next(e for e in events if '"done"' in e)
    assert '"trace_id"' in done and request_id in done


def test_chat_cache_write_reuses_stream_rewritten_query(client, monkeypatch):
    """缓存回填采用 RAG 流提供的改写 key，不在 Chat 层重复调用 rewrite。"""
    tc, _, _ = client
    writes = []

    async def _fake(*_a, **_k):
        yield ("intent", {"intent": "qa"})
        yield ("token", {"delta": "保修12个月"})
        yield ("sources", {"sources": []})
        yield ("done", {"message_id": "", "rewritten_query": "规范化后的问题"})

    def _unexpected_rewrite(*_a, **_k):
        raise AssertionError("Chat 层不应重复 rewrite")

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    monkeypatch.setattr("app.api.chat.rewrite", _unexpected_rewrite, raising=False)
    monkeypatch.setattr("app.api.chat.cache_put", lambda *args: writes.append(args))

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "碎屏显咋换", "stream": True},
        headers=_headers(),
    )

    assert r.status_code == 200
    assert writes and writes[0][0] == "规范化后的问题"


def test_chat_cache_fill_skipped_when_state_hint(client, monkeypatch):
    """P2-③：会话状态影响回答（state_hint 非空）→ 不进全局缓存（正确性优先）。"""
    tc, _, _ = client
    writes = []

    async def _fake(*_a, **_k):
        yield ("intent", {"intent": "qa"})
        yield ("token", {"delta": "答复"})
        yield ("sources", {"sources": []})
        yield ("done", {"message_id": "", "rewritten_query": "q1"})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    monkeypatch.setattr("app.api.chat.cache_put", lambda *args: writes.append(args))
    # 会话有主题（无订单号）→ to_prompt_hint 非 None → 回填被拦截
    monkeypatch.setattr(
        "app.api.chat._update_conv_state_locked",
        lambda db, sid, msg: {"topic": "退款", "slots": {}, "clarify_count": 0, "stage": "collecting"},
    )

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "退款到账多久", "stream": True},
        headers=_headers(),
    )

    assert r.status_code == 200
    assert writes == [], f"state_hint 非空不应回填全局缓存: {writes}"


def test_chat_cache_fill_skipped_when_user_profile(client, monkeypatch):
    """P2-③：个性化用户（画像非空）→ 不进精确层全局缓存（正确性优先）。"""
    tc, _, _ = client
    writes = []

    async def _fake(*_a, **_k):
        yield ("intent", {"intent": "qa"})
        yield ("token", {"delta": "答复"})
        yield ("sources", {"sources": []})
        yield ("done", {"message_id": "", "rewritten_query": "q2"})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    monkeypatch.setattr("app.api.chat.cache_put", lambda *args: writes.append(args))
    from app.core.config import settings as cfg_settings

    monkeypatch.setattr(cfg_settings, "USER_PROFILE_ENABLED", True)
    monkeypatch.setattr(
        "app.api.chat.get_profile",
        lambda db, uid: {"topics": {"退款": 5}, "entities": ["SO2026080118"]},
    )

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "退款到账多久", "stream": True},
        headers=_headers(),
    )

    assert r.status_code == 200
    assert writes == [], f"用户画像非空不应回填全局缓存: {writes}"


def test_chat_quick_answer_marks_source_and_honest_stage(client, monkeypatch):
    """快捷话术短路可辨 + stage 诚实（2026-08-25 溯源空面板排查）。

    命中 match_quick 预置话术时：不检索、秒回固定文案、sources 恒空。
    - done/meta 落 answer_source="quick"（前端 SourcePanel 区分「预置话术无引用」与「暂无引用」；
      admin 后续可统计快捷命中率）
    - stage 进度提示不再谎报「已检索知识库」（该分支不检索，诚实标注）
    """
    tc, Local, _ = client
    monkeypatch.setattr("app.api.chat.match_quick", lambda content: "预置答案：保修12个月")

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "保修多久？", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    events = [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]
    # 诚实性：quick 分支不检索，进度提示不得出现「已检索知识库」
    assert all("已检索知识库" not in e for e in events)
    done = next(e for e in events if '"done"' in e)
    assert '"answer_source"' in done and '"quick"' in done

    # 落库可辨：meta.answer_source = quick
    with Local() as db:
        assistant = db.scalars(
            select(Message).where(Message.role == MessageRole.assistant)
        ).one()
        assert assistant.meta.get("answer_source") == "quick"


def test_chat_stream_quota_exceeded_no_llm(client, monkeypatch):
    tc, Local, calls = client

    class EmptyQuota:
        def left_today(self, _uid):
            return 0

        def try_consume(self, _uid, n=1, idem_key=None):  # idem_key：与生产签名对齐（配额幂等键，2026-08-20 补）
            return (False, 0)  # 超限 → 闸门拒绝

    monkeypatch.setattr("app.api.chat.get_quota_service", lambda: EmptyQuota())
    called = []

    async def _fake(*_a, **_k):
        called.append(1)
        yield ("token", {"delta": "x"})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "退货运费谁出", "stream": True},
        headers=_headers(),
    )
    # P4：超额统一走 HTTP 429 + detail（不再 HTTP200+SSE error 双面不一致）；未调 LLM
    assert r.status_code == 429
    assert "今日问答额度已用完" in r.text
    assert called == []  # 未调 LLM


def test_chat_stream_error_event_refunds_quota(client, monkeypatch):
    """S1（外部审查 2026-08-28）：SSE error 事件路径必须退配额。

    rag_service 两个 error 源（RAG_RETRIEVAL/RAG_GENERATE）都在配额扣减之后触发——
    error 即「已扣费但未交付回答」，配额必须回滚，否则用户额度被静默侵蚀。
    断言：error 事件发生后净消耗归零（try_consume +1 被 refund 抵消，用户可再次消费）；
    且 error 路径不落 assistant 消息（未交付不落库）。
    """
    tc, Local, calls = client

    class _ErrorStream:
        """替身 stream_answer：检索正常后生成失败（对齐 rag_service :259 真实序列）。"""

        @staticmethod
        async def __call__(query, kb_id, history=None, top_k=5, **kwargs):
            yield ("stage", {"stage": "retrieving", "msg": "已检索知识库"})
            yield ("stage", {"stage": "generating", "msg": "正在生成回答"})
            yield ("error", {"code": "RAG_GENERATE", "message": "回答生成失败，请稍后重试"})

    monkeypatch.setattr("app.api.chat.stream_answer", _ErrorStream())

    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "退货运费谁出", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert '"event": "error"' in r.text and "RAG_GENERATE" in r.text

    # S1 核心：error 路径退款 → 净消耗归零（修复前恒为 1——额度被静默侵蚀）
    assert calls["consumed"] == 0

    # 未交付不落 assistant 消息（仅 user 消息在库）
    with Local() as db:
        msgs = db.scalars(select(Message)).all()
        assert [m.role for m in msgs] == [MessageRole.user]


def test_chat_stream_foreign_session_404(client):
    tc, *_ = client
    # 他人 session（user 不同，表中不存在）
    r = tc.post(
        f"{API}/chat/stream",
        json={
            "session_id": "99999999-9999-9999-9999-999999999999",
            "content": "保修多久",
            "stream": True,
        },
        headers=_headers(),
    )
    assert r.status_code == 404


def test_chat_stream_unauthenticated_401(client):
    tc, *_ = client
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "hi", "stream": True},
    )
    assert r.status_code == 401


def test_sessions_crud(client):
    tc, *_ = client
    # 创建
    r = tc.post(f"{API}/sessions", json={"title": "我的会话"}, headers=_headers())
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["title"] == "我的会话"
    # 列表
    r = tc.get(f"{API}/sessions", headers=_headers())
    assert r.status_code == 200
    assert any(i["session_id"] == sid for i in r.json()["items"])
    # 详情（M8：返回 SessionDetail，含 messages，id 字段）
    r = tc.get(f"{API}/sessions/{sid}", headers=_headers())
    assert r.status_code == 200 and r.json()["id"] == sid
    assert "messages" in r.json()


def test_session_ownership_agent_can_read_other_user(client):
    """R-1：agent/admin 可读任意用户会话（客服查看场景）；非所有者 user 仍 403。"""
    tc, *_ = client
    r = tc.post(f"{API}/sessions", json={"title": "用户会话"}, headers=_headers())
    sid = r.json()["session_id"]

    agent_h = {
        "Authorization": f"Bearer {create_access_token('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'agent')}"
    }
    assert tc.get(f"{API}/sessions/{sid}", headers=agent_h).status_code == 200

    admin_h = {
        "Authorization": f"Bearer {create_access_token('cccccccc-cccc-cccc-cccc-cccccccccccc', 'admin')}"
    }
    assert tc.get(f"{API}/sessions/{sid}", headers=admin_h).status_code == 200

    other_user_h = {
        "Authorization": f"Bearer {create_access_token('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'user')}"
    }
    assert tc.get(f"{API}/sessions/{sid}", headers=other_user_h).status_code == 403


def test_chat_stream_handoff_creates_ticket(client, monkeypatch):
    """T1：intent=handoff → AI 建单（幂等 + 溯源锚点 message_id），done 带 ticket_id。"""
    tc, Local, _ = client

    async def _fake(*_a, **_k):
        yield ("intent", {"intent": "handoff"})
        yield ("stage", {"stage": "retrieving"})
        yield ("token", {"delta": "已为您转接人工"})
        yield ("done", {"message_id": ""})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "我要投诉找经理", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert '"ticket_id"' in r.text  # done 事件携带工单号

    with Local() as db:
        tickets = db.scalars(select(Ticket)).all()
        assert len(tickets) == 1
        assert tickets[0].status == TicketStatus.open
        assert tickets[0].message_id is not None  # T1 溯源锚点已落
        # Bug #1 修复：user 消息 intent 已回写为 handoff（此前恒 qa → F1 hot_gaps 数据源失效）
        user_msg = db.scalars(
            select(Message).where(
                Message.role == MessageRole.user,
                Message.session_id == uuid.UUID("11111111-1111-1111-1111-111111111111"),
            )
        ).first()
        assert user_msg is not None and user_msg.intent == "handoff"


def test_chat_stream_handoff_persists_summary(client, monkeypatch):
    """架构一期 4：handoff 建单持久化移交摘要（当前诉求 + conv_state 主题跨轮兜底）。

    取材 = history + 当前消息：_fetch_history 排除当前 user_msg，而「转人工」这条触发
    移交的消息本身是坐席最需要的诉求，单轮移交时 history 为空也必须有摘要。
    注意两轮都不带订单号：一旦 conv_state 有 order_no + 订单类主题，订单工具分支
    （零 LLM 模板）会短路整条流，handoff 事件永远到不了。
    """
    tc, Local, _ = client

    async def _qa(*_a, **_k):
        yield ("intent", {"intent": "qa"})
        yield ("stage", {"stage": "generating"})
        yield ("token", {"delta": "已为您记录"})
        yield ("done", {"message_id": ""})

    async def _handoff(*_a, **_k):
        yield ("intent", {"intent": "handoff"})
        yield ("stage", {"stage": "retrieving"})
        yield ("token", {"delta": "已为您转接人工"})
        yield ("done", {"message_id": ""})

    # 第一轮（qa）：表达退款诉求——conv_state 记下 topic=退款（跨轮保留）
    monkeypatch.setattr("app.api.chat.stream_answer", _qa)
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "我的退款到底还要多久", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200

    # 第二轮（handoff）：触发转人工（本身无主题词）
    monkeypatch.setattr("app.api.chat.stream_answer", _handoff)
    r2 = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "转人工，气死了", "stream": True},
        headers=_headers(),
    )
    assert r2.status_code == 200

    with Local() as db:
        tickets = db.scalars(select(Ticket)).all()
        assert len(tickets) == 1  # 仅第二轮建单（qa 不建单）
        summary = tickets[0].summary
        assert summary
        assert "退款" in summary  # 主题（conv_state 兜底：触发消息本身无主题词）
        assert "转人工" in summary  # 当前诉求（history 排除当前消息 → 取材含当前）


def test_chat_stream_refuse_intent_persisted(client, monkeypatch):
    """H2（外部审查 2026-08-22）：拒答必须以 intent='refuse' 落库。

    classify_intent 只返回 qa/handoff/chitchat，refuse 是 intent 事件里的布尔标志——
    若落库只写 data['intent']，生产链路永远不产生 refuse 行，admin 待补录 Top10 恒空。"""

    async def _fake(*_a, **_k):
        yield ("intent", {"intent": "qa", "refuse": True})
        yield ("stage", {"stage": "generating"})
        yield ("token", {"delta": "未收录该问题"})
        yield ("done", {"message_id": ""})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    tc, Local, _ = client
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "你们多久上市", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200

    with Local() as db:
        user_msg = db.scalars(
            select(Message).where(
                Message.role == MessageRole.user,
                Message.session_id == uuid.UUID("11111111-1111-1111-1111-111111111111"),
            )
        ).first()
        assert user_msg is not None and user_msg.intent == "refuse"


def test_agent_can_reply_on_user_session(client, monkeypatch):
    """T5：agent 代答——可对用户会话 chat/stream（记录 agent_id）。

    人工直复已迁移至 POST /sessions/{id}/messages（Branch 3，见 test_sessions_messages.py）；
    原 /chat/reply 端点已删除（前端零调用，且与新端点 role=agent 语义冲突）。
    """
    tc, Local, _ = client
    agent_h = {
        "Authorization": f"Bearer {create_access_token('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'agent')}"
    }

    # 代答流式：agent 向用户 session 发问 → 200 + user 消息带 agent_id
    async def _fake(*_a, **_k):
        yield ("intent", {"intent": "qa"})
        yield ("token", {"delta": "好的"})
        yield ("done", {"message_id": ""})

    monkeypatch.setattr("app.api.chat.stream_answer", _fake)
    r = tc.post(
        f"{API}/chat/stream",
        json={"session_id": "11111111-1111-1111-1111-111111111111", "content": "我来帮您查询", "stream": True},
        headers=agent_h,
    )
    assert r.status_code == 200
    with Local() as db:
        agent_msg = db.scalars(select(Message).where(Message.role == MessageRole.user)).all()[-1]
        assert agent_msg.meta.get("agent_id") == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_sse_event_whitelist():
    """C2：SSE 事件名白名单——合法事件可编码，越界降级为 error 事件（不掐断流）。"""
    from app.api.chat import _sse

    for ev in ("stage", "intent", "token", "sources", "done", "error"):
        assert f'"event": "{ev}"' in _sse({"event": ev, "data": {}})
    out = _sse({"event": "typo_event", "data": {}})
    assert '"event": "error"' in out and "SSE_CONTRACT" in out  # fail-open：error 事件而非 raise


def test_sse_events_match_frontend_contract():
    """C2 闭环：后端 chat SSE 事件名须被前端契约声明（SSEEvent 子集）。

    契约含多个 SSE 类型（chat SSEEvent / 通知 NotifySSEEvent），
    故为**子集**断言：后端每个 chat 事件都须在前端契约中存在（防新增事件前端未声明），
    同时允许前端存在其他 SSE 类型的事件字面量。
    """
    import re as _re
    from pathlib import Path

    from app.api.chat import _SSE_EVENTS

    backend_events = set(_SSE_EVENTS)
    # 契约单一真源：frontend/src/contracts/api.ts 已是 re-export 桥（无类型字面量），
    # 直接读根 contracts/api.ts（SSEEvent union 所在处）
    contract_path = Path(__file__).resolve().parent.parent.parent / "contracts" / "api.ts"
    if not contract_path.exists():
        pytest.skip("contracts/api.ts 不存在（仅后端子目录 CI 场景）")
    text = contract_path.read_text(encoding="utf-8")
    frontend_events = set(_re.findall(r"event: '(\w+)'", text))
    missing = backend_events - frontend_events
    assert not missing, (
        f"SSE 契约漂移：后端 chat 事件 {sorted(missing)} 前端契约未声明"
    )


def test_session_satisfaction(client):
    """P2-2：会话满意度评分——幂等覆盖 + 非法值 422 + 越权 404 防探测。"""
    tc = client[0]
    sid = tc.post(f"{API}/sessions", json={"title": "满意度"}, headers=_headers()).json()["session_id"]
    # 正常评分 + 幂等覆盖
    assert tc.post(f"{API}/sessions/{sid}/satisfaction", json={"rating": "satisfied"}, headers=_headers()).status_code == 200
    assert tc.post(f"{API}/sessions/{sid}/satisfaction", json={"rating": "unsatisfied"}, headers=_headers()).status_code == 200
    # 非法值 → 422
    assert tc.post(f"{API}/sessions/{sid}/satisfaction", json={"rating": "meh"}, headers=_headers()).status_code == 422
    # 越权：别人的会话 → 404（防探测）
    other_sid = tc.post(f"{API}/sessions", json={}, headers={
        "Authorization": f"Bearer {create_access_token('99999999-9999-9999-9999-999999999999', 'user')}"
    }).json()["session_id"]
    assert tc.post(f"{API}/sessions/{other_sid}/satisfaction", json={"rating": "neutral"}, headers=_headers()).status_code == 404


def test_chat_updates_conv_state(client, monkeypatch):
    """批次B：两轮对话驱动 conv_state——首轮退款主题→collecting，次轮补订单号→resolving。"""
    c, Local, _ = client
    # 批次D 起订单工具会截胡「订单主题+订单号」消息（查单命中即短路）——置空缓存
    # 让查单 miss 回落 RAG，本用例专注批次B state_hint 透传（工具分支由 order_tool 系列用例覆盖）
    import app.services.tools.order_tool as _ot

    monkeypatch.setattr(_ot, "_ORDERS_CACHE", {})
    captured_kwargs: dict = {}

    class _CaptureStream(_FakeStream):
        @staticmethod
        async def __call__(query, kb_id, history=None, top_k=5, **kwargs):
            captured_kwargs.update(kwargs)
            async for ev in _FakeStream.__call__(query, kb_id, history=history, top_k=top_k, **kwargs):
                yield ev

    monkeypatch.setattr("app.api.chat.stream_answer", _CaptureStream())
    sid = "11111111-1111-1111-1111-111111111111"

    # 第一轮：退款主题，无订单号
    r1 = c.post(
        f"{API}/chat/stream",
        json={"session_id": sid, "content": "我要退款", "stream": True},
        headers=_headers(),
    )
    assert r1.status_code == 200

    with Local() as db:
        s = db.scalar(select(Session).where(Session.id == uuid.UUID(sid)))
        assert s.conv_state is not None
        assert s.conv_state["topic"] == "退款"
        assert s.conv_state["stage"] == "info_collecting"

    # 第二轮：补订单号
    r2 = c.post(
        f"{API}/chat/stream",
        json={"session_id": sid, "content": "订单号 SO2026080118", "stream": True},
        headers=_headers(),
    )
    assert r2.status_code == 200

    with Local() as db:
        s = db.scalar(select(Session).where(Session.id == uuid.UUID(sid)))
        assert s.conv_state["slots"]["order_no"] == "SO2026080118"
        assert s.conv_state["stage"] == "resolving"

    # state_hint 已透传给 stream_answer（第二轮含订单号）
    assert "state_hint" in captured_kwargs
    assert captured_kwargs["state_hint"] is not None
    assert "SO2026080118" in captured_kwargs["state_hint"]


def test_chat_conv_state_fail_open(client, monkeypatch):
    """批次B：状态写库异常不阻断问答（fail-open）——mock update 抛错，流照常完成。"""
    c, _, _ = client
    monkeypatch.setattr(
        "app.api.chat.conversation_state.update",
        lambda state, message: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    sid = "11111111-1111-1111-1111-111111111111"
    r = c.post(
        f"{API}/chat/stream",
        json={"session_id": sid, "content": "我要退款", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200  # fail-open：异常不 5xx
    body = r.text
    assert "SYS_ERROR" not in body or "token" in body  # 流正常产出（token 事件存在）


def test_chat_clarify_flow_updates_state(client, monkeypatch):
    """批次C：拒答轮（有澄清额度）→ done.clarify → conv_state 置 clarifying + 计数+1。"""
    c, Local, _ = client

    class _RefuseThenClarifyStream:
        """替身 stream_answer：模拟拒答且触发澄清（done 带 clarify=True）。

        大扫查修正（2026-08-25）：事件序列对齐 rag_service.py:182 真实分支——
        澄清轮 emit refuse=False（intent 不标拒答，批次C 有意设计）。旧写法
        refuse=True 在真实流中不可达，曾使下方面向「澄清轮计拒答」的错误假设假绿。"""

        @staticmethod
        async def __call__(query, kb_id, history=None, top_k=5, **kwargs):
            yield ("intent", {"intent": "qa", "refuse": False})
            yield ("stage", {"stage": "retrieving", "msg": "已检索知识库"})
            yield ("stage", {"stage": "generating", "msg": "正在生成回答"})
            yield ("token", {"delta": "您是想咨询退货流程还是退款到账？"})
            yield ("sources", {"sources": []})
            yield ("done", {"message_id": "", "clarify": True})

    monkeypatch.setattr("app.api.chat.stream_answer", _RefuseThenClarifyStream())
    sid = "11111111-1111-1111-1111-111111111111"
    r = c.post(
        f"{API}/chat/stream",
        json={"session_id": sid, "content": "怎么退货", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert '"clarify": true' in r.text.replace("'", '"') or '"clarify":true' in r.text.replace(" ", "")

    with Local() as db:
        s = db.scalar(select(Session).where(Session.id == uuid.UUID(sid)))
        assert s.conv_state is not None
        assert s.conv_state["stage"] == "clarifying"
        assert s.conv_state["clarify_count"] == 1
        # 大扫查口径锁定（2026-08-25）：澄清轮 refuse=False → user intent 落 'qa'，
        # 不计入 admin refuse_count——refuse_count 即真拒答轮数，无需再减 clarify_rounds。
        u_msg = db.scalars(
            select(Message).where(
                Message.role == MessageRole.user,
                Message.session_id == uuid.UUID(sid),
            )
        ).first()
        assert u_msg is not None
        assert u_msg.intent == "qa"
        # T1.1：澄清轮 assistant 落库必须带 meta.clarify=True——运营观测澄清轮数的唯一原料
        a_msg = db.scalars(
            select(Message).where(
                Message.role == MessageRole.assistant,
                Message.session_id == uuid.UUID(sid),
            )
        ).first()
        assert a_msg is not None
        assert a_msg.meta is not None and a_msg.meta.get("clarify") is True


def test_chat_clarify_left_decrements_across_rounds(client, monkeypatch):
    """批次C：额度耗尽（clarify_count=2）→ 拒答轮不再请求澄清（clarify_left=0 透传）。"""
    c, Local, _ = client
    captured: dict = {}

    class _CaptureStream:
        @staticmethod
        async def __call__(query, kb_id, history=None, top_k=5, **kwargs):
            captured.update(kwargs)
            yield ("intent", {"intent": "qa", "refuse": True})
            yield ("token", {"delta": "建议转人工"})
            yield ("sources", {"sources": []})
            yield ("done", {"message_id": ""})

    monkeypatch.setattr("app.api.chat.stream_answer", _CaptureStream())
    sid = "11111111-1111-1111-1111-111111111111"
    # 预置：已用完澄清额度
    with Local() as db:
        s = db.scalar(select(Session).where(Session.id == uuid.UUID(sid)))
        s.conv_state = {"stage": "clarifying", "topic": "退换货", "slots": {}, "clarify_count": 2}
        db.commit()

    r = c.post(
        f"{API}/chat/stream",
        json={"session_id": sid, "content": "还是不清楚", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert captured.get("clarify_left") == 0  # 额度耗尽，禁止澄清


def test_chat_order_tool_branch(client, monkeypatch, tmp_path):
    """批次D：槽位订单号+订单主题 → 工具分支零 LLM 回答（meta 带 tool 标记）。"""
    import json as _json

    c, Local, _ = client
    data_file = tmp_path / "orders.json"
    data_file.write_text(
        _json.dumps({
            "orders": [{
                "order_no": "SO2026080118", "status": "shipped", "items": "空气净化器 K1",
                "logistics_no": "SF1384429007712", "eta": "2026-08-26 前送达", "updated_at": "x",
            }]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    import app.services.tools.order_tool as ot
    monkeypatch.setattr(ot, "_DATA_FILE", data_file)
    monkeypatch.setattr(ot, "_ORDERS_CACHE", None)
    monkeypatch.setattr("app.api.chat.order_tool._DATA_FILE", data_file)
    monkeypatch.setattr("app.api.chat.order_tool._ORDERS_CACHE", None)

    sid = "11111111-1111-1111-1111-111111111111"
    # 一条消息同时含主题与订单号 → 槽位即时填充 → 工具分支
    r = c.post(
        f"{API}/chat/stream",
        json={"session_id": sid, "content": "订单 SO2026080118 物流到哪了", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert "已发货" in r.text
    assert "SO2026080118" in r.text
    assert "SF1384429007712" in r.text

    # 落库 meta 带 tool 标记
    with Local() as db:
        from app.models.message import Message as M
        msgs = db.scalars(select(M).where(M.session_id == uuid.UUID(sid), M.role == "assistant")).all()
        assert msgs
        assert msgs[-1].meta and msgs[-1].meta.get("tool") == "order_query"


def test_chat_order_tool_miss_falls_back_rag(client, monkeypatch):
    """批次D：订单号查不到 → 回落 RAG 正常流（不阻断）。"""
    c, _, _ = client
    import app.services.tools.order_tool as ot
    monkeypatch.setattr(ot, "_ORDERS_CACHE", {})
    monkeypatch.setattr("app.api.chat.order_tool._ORDERS_CACHE", {})

    sid = "11111111-1111-1111-1111-111111111111"
    r = c.post(
        f"{API}/chat/stream",
        json={"session_id": sid, "content": "订单 SO9999999999 退款到哪一步了", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200  # 回落 RAG（mock 的 _FakeStream 事件流照常）


def test_chat_order_tool_no_slot_skips(client, monkeypatch):
    """批次D：无订单号槽位（纯主题消息）→ 不走工具分支（走 RAG）。"""
    c, _, _ = client
    called = {"n": 0}

    import app.api.chat as chat_mod
    orig = chat_mod.order_tool.query_order
    monkeypatch.setattr(
        "app.api.chat.order_tool.query_order",
        lambda no: (called.__setitem__("n", called["n"] + 1), orig(no))[1],
    )
    sid = "11111111-1111-1111-1111-111111111111"
    r = c.post(
        f"{API}/chat/stream",
        json={"session_id": sid, "content": "退款多久到账", "stream": True},
        headers=_headers(),
    )
    assert r.status_code == 200
    assert called["n"] == 0  # 工具未被调用


# ============ v1.3 图片集成：fused_query 进 QA（P0-3 锁定） ============

class _FakeVisionClient:
    """mock 火山视觉模型：describe_image 返回固定描述，记录调用。"""

    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    async def describe_image(self, image_path, text_query=None):
        self.calls.append((str(image_path), text_query))
        return "屏幕碎裂的手机屏幕"


def _capture_stream(seen: dict):
    async def _fake_stream(query, kb_id, history=None, top_k=5, **kwargs):
        seen["query"] = query
        yield ("intent", {"intent": "qa"})
        yield ("token", {"delta": "保修12个月"})
        yield ("sources", {"sources": []})
        yield ("done", {"message_id": ""})

    return _fake_stream


def test_chat_image_fused_query_feeds_qa(client, monkeypatch, tmp_path):
    """P0-3：带图请求 → Router 排 Image Agent 先行 → fused_query 进 QA。

    锁定：图片描述与用户问题融合后进入 stream_answer（检索/缓存键同源）。
    此前 router 用 image_refs 判断（恒空）而 chat 层注入 image_paths——
    字段错位导致图片请求实际走纯文本 QA，本用例即回归保险丝。
    """
    import app.services.agents.image_agent as _img_mod
    from app.core.config import settings

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    img = upload_dir / "screen.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)  # 白名单内合法图片（<10MB）
    monkeypatch.setattr(settings, "IMAGE_UPLOAD_DIR", str(upload_dir))

    fake_vision = _FakeVisionClient()
    monkeypatch.setattr(_img_mod, "get_vision_client", lambda: fake_vision)

    c, _, _ = client
    seen: dict = {}
    monkeypatch.setattr("app.api.chat.stream_answer", _capture_stream(seen))

    r = c.post(
        f"{API}/chat/stream",
        json={
            "session_id": "11111111-1111-1111-1111-111111111111",
            "content": "看看这个手机屏幕",
            "stream": True,
            "image_paths": [str(img)],
        },
        headers=_headers(),
    )
    assert r.status_code == 200
    # Image Agent 真实执行（视觉模型被调用）
    assert len(fake_vision.calls) == 1
    # fused_query 进入 QA：用户问题 + 图片描述融合文本
    assert seen["query"] == "看看这个手机屏幕（图片内容：屏幕碎裂的手机屏幕）"


def test_chat_no_image_uses_raw_content(client, monkeypatch):
    """回归：无图请求 fused_query 为空 → stream_answer 收到原始 query。"""
    c, _, _ = client
    seen: dict = {}
    monkeypatch.setattr("app.api.chat.stream_answer", _capture_stream(seen))

    r = c.post(
        f"{API}/chat/stream",
        json={
            "session_id": "11111111-1111-1111-1111-111111111111",
            "content": "退货运费谁出",
            "stream": True,
            "image_paths": [],
        },
        headers=_headers(),
    )
    assert r.status_code == 200
    assert seen["query"] == "退货运费谁出"
