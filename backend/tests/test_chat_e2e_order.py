"""订单链路 e2e（批次D）：主题→澄清→槽位→工具接管 的三步闭环。

复用 test_chat_api 的夹具模式；stream_answer 用真实 rag_service（拒答路径）+
mock generate_clarify，验证批次 B/C/D 三个系统的串联行为。
"""
from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.knowledge import Document, KnowledgeBase
from app.models.message import Message, MessageSource
from app.models.session import Session
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def client(monkeypatch, tmp_path):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(engine, tables=[Session.__table__, Message.__table__, MessageSource.__table__, KnowledgeBase.__table__, Document.__table__, User.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    with Local() as db:
        db.add(Session(id=SID, user_id=USER_ID))
        db.add(KnowledgeBase(id=uuid.UUID("33333333-3333-3333-3333-333333333333"), name="e2e库"))
        db.commit()

    # 配额 mock
    class FakeQuota:
        def try_consume(self, *a, **k):
            return (True, 0)

        def refund(self, *a, **k):
            return 0

    monkeypatch.setattr("app.api.chat.get_quota_service", lambda: FakeQuota())
    monkeypatch.setattr("app.api.chat._latest_kb_id", lambda db: uuid.UUID("33333333-3333-3333-3333-333333333333"))

    # 检索强制低分拒答（触发澄清链）——整体 patch search_kb（steps/retrieve 函数内
    # from-import 每次取模块属性，patch 模块级函数即生效），绕过 Qdrant/embedding；
    # dense_score=0.05 < MIN_SCORE(0.30) → check_refuse 置 refuse
    import app.services.retrieval_service as retrieval_service

    def _low_score_search(query, kb_id, top_k=8):
        return [
            retrieval_service.RetrievedChunk(
                chunk_id="c", doc_id="d", kb_id=str(kb_id), idx=0,
                text="无关内容", score=0.05, dense_score=0.05,
            )
        ]

    monkeypatch.setattr(retrieval_service, "search_kb", _low_score_search)

    # 澄清问句 mock（真实 rag_service 拒答路径 + clarify 分支）
    import app.services.rag_service as rs

    async def _fake_clarify(query, chunks):
        return "请问您提供一下订单号，我为您查询物流状态？"

    monkeypatch.setattr(rs, "generate_clarify", _fake_clarify)

    # 订单数据源
    import app.services.tools.order_tool as ot
    data_file = tmp_path / "orders.json"
    data_file.write_text(json.dumps({
        "orders": [{"order_no": "SO2026080118", "status": "shipped", "items": "空气净化器 K1",
                    "logistics_no": "SF1384429007712", "eta": None, "updated_at": "x"}]
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(ot, "_DATA_FILE", data_file)
    monkeypatch.setattr(ot, "_ORDERS_CACHE", None)
    monkeypatch.setattr("app.api.chat.order_tool._DATA_FILE", data_file)
    monkeypatch.setattr("app.api.chat.order_tool._ORDERS_CACHE", None)

    with TestClient(app) as c:
        yield c, Local
    app.dependency_overrides.clear()


def _h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def _sse_text(response_text: str) -> str:
    """拼接 SSE 流里全部 token delta（澄清分支仍走 8 字分片，跨片关键词不能对原始文本做子串断言）。"""
    parts: list[str] = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[len("data: "):])
            if data.get("event") == "token":
                parts.append(data["data"]["delta"])
    return "".join(parts)


def test_order_e2e_clarify_then_tool(client):
    """三步闭环：①主题无单号→拒答澄清 ②给单号→槽位填充 ③订单主题→工具回答。"""
    c, Local = client
    sid = str(SID)

    # 第①步：物流主题、无订单号 → 拒答 + 澄清（done.clarify）
    r1 = c.post(f"{API}/chat/stream", json={"session_id": sid, "content": "物流到哪了", "stream": True}, headers=_h())
    assert r1.status_code == 200
    assert "订单号" in _sse_text(r1.text)  # 澄清问句

    with Local() as db:
        s = db.scalar(select(Session).where(Session.id == SID))
        assert s.conv_state["topic"] == "配送/物流"
        assert s.conv_state["stage"] in ("info_collecting", "clarifying")

    # 第②步：给订单号（主题延续）→ 槽位填充
    r2 = c.post(f"{API}/chat/stream", json={"session_id": sid, "content": "SO2026080118", "stream": True}, headers=_h())
    assert r2.status_code == 200

    with Local() as db:
        s = db.scalar(select(Session).where(Session.id == SID))
        assert s.conv_state["slots"]["order_no"] == "SO2026080118"

    # 第③步：订单主题消息 → 工具分支零 LLM 回答
    r3 = c.post(f"{API}/chat/stream", json={"session_id": sid, "content": "帮我看下物流进度", "stream": True}, headers=_h())
    assert r3.status_code == 200
    assert "已发货" in _sse_text(r3.text)
    assert "SF1384429007712" in _sse_text(r3.text)
