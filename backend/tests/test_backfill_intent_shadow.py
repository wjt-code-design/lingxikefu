"""backfill_intent_shadow 回填脚本单测（意图切换数据依赖加速，批次 I）。

背景：影子样本靠 20% 在线采样积累极慢（实测 18 天仅 7 条），离线回填把历史
rule=qa 的用户消息补跑 LLM 影子分类，使 ≥500 门槛评估成为可能（真实历史流量，
非合成数据）。

覆盖：dry-run 零副作用 / 只碰 qa 无标记 / 已标记不重复 / LLM 失败不中断 /
limit / 汇总统计。手法同 test_intent_shadow：SQLite + StaticPool，fake client 注入。
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from app.models.base import Base
from app.models.message import Message, MessageRole
from scripts.backfill_intent_shadow import run_backfill
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

TENANT = "default"


class _FakeChatClient:
    """非流式 complete 替身：按内容包含关键词返回不同意图；可注入异常。"""

    def __init__(self, fail_keywords: tuple[str, ...] = ()):
        self._fail_keywords = fail_keywords
        self.calls = 0

    async def complete(self, messages, **kwargs) -> str:
        self.calls += 1
        text = messages[-1]["content"]
        if any(k in text for k in self._fail_keywords):
            raise RuntimeError("boom")
        return '{"intent": "chitchat"}'


def _sqlite() -> sessionmaker:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for c in Message.__table__.columns:
        if c.name == "meta":
            c.type = sa.JSON()
            c.server_default = None
    Base.metadata.create_all(engine, tables=[Message.__table__])
    return sessionmaker(bind=engine, expire_on_commit=False)


def _mk(factory, *, intent: str | None, shadowed: bool, content: str = "退货政策是什么") -> uuid.UUID:
    mid = uuid.uuid4()
    with factory() as db:
        db.add(
            Message(
                id=mid,
                session_id=uuid.uuid4(),
                tenant_id=TENANT,
                role=MessageRole.user,
                content=content,
                intent=intent,
                meta={"intent_shadow": {"intent": "qa", "latency_ms": 5}} if shadowed else {},
            )
        )
        db.commit()
    return mid


def _msgs(factory) -> list[Message]:
    with factory() as db:
        return db.execute(select(Message)).scalars().all()


def test_dry_run_zero_side_effects():
    factory = _sqlite()
    _mk(factory, intent="qa", shadowed=False)
    cli = _FakeChatClient()
    out = run_backfill(session_factory=factory, client=cli, dry_run=True, sleep_s=0)
    assert out == {"candidates": 1, "backfilled": 0, "failed": 0}
    assert cli.calls == 0  # dry-run 不调 LLM
    assert all(m.meta.get("intent_shadow") in (None, {}) for m in _msgs(factory))


def test_backfill_only_qa_unshadowed():
    factory = _sqlite()
    qa_mid = _mk(factory, intent="qa", shadowed=False)
    qa_done_mid = _mk(factory, intent="qa", shadowed=True)
    _mk(factory, intent="handoff", shadowed=False, content="转人工")
    _mk(factory, intent="chitchat", shadowed=False, content="你好呀")
    cli = _FakeChatClient()
    out = run_backfill(session_factory=factory, client=cli, sleep_s=0)
    assert out == {"candidates": 1, "backfilled": 1, "failed": 0}
    assert cli.calls == 1
    msgs = {m.id: m for m in _msgs(factory)}
    assert msgs[qa_mid].meta["intent_shadow"]["intent"] == "chitchat"
    # handoff/chitchat/已标记：一律不碰
    assert msgs[qa_done_mid].meta["intent_shadow"]["latency_ms"] == 5  # 原值未被改写
    for m in _msgs(factory):
        if m.intent in ("handoff", "chitchat"):
            assert "intent_shadow" not in (m.meta or {})


def test_llm_failure_does_not_abort():
    factory = _sqlite()
    _mk(factory, intent="qa", shadowed=False, content="这条会失败")
    _mk(factory, intent="qa", shadowed=False, content="这条会成功")
    cli = _FakeChatClient(fail_keywords=("失败",))
    out = run_backfill(session_factory=factory, client=cli, sleep_s=0)
    assert out["candidates"] == 2
    assert out["backfilled"] == 1
    assert out["failed"] == 1
    done = [m for m in _msgs(factory) if "成功" in m.content]
    assert done[0].meta["intent_shadow"]["intent"] == "chitchat"


def test_limit_bounds_work():
    factory = _sqlite()
    for i in range(3):
        _mk(factory, intent="qa", shadowed=False, content=f"问题{i}")
    cli = _FakeChatClient()
    out = run_backfill(session_factory=factory, client=cli, limit=2, sleep_s=0)
    assert out == {"candidates": 2, "backfilled": 2, "failed": 0}


def test_unparseable_llm_output_counts_failed():
    # parse_intent 返回 None（越界意图）→ 不落库 → failed
    factory = _sqlite()
    _mk(factory, intent="qa", shadowed=False)

    class _BadClient:
        calls = 0

        async def complete(self, messages, **kwargs):
            _BadClient.calls += 1
            return '{"intent": "world_knowledge"}'

    out = run_backfill(session_factory=factory, client=_BadClient(), sleep_s=0)
    assert out == {"candidates": 1, "backfilled": 0, "failed": 1}
    assert all("intent_shadow" not in (m.meta or {}) for m in _msgs(factory))
