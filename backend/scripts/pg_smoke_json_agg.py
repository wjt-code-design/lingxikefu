"""一次性 PG 冒烟脚本（大扫查 major-B，2026-08-25）：admin 观测聚合的 PG 方言实测。

背景：test_admin_stats.py 全部跑在 sqlite :memory:（meta 就地换 sa.JSON），
.as_string()/.as_boolean() 的 PG 编译产物（->> 提取 / CAST 文本转布尔）
此前从未在任何环境真正执行过。本脚本在真实 PG16 上建表（原生 JSONB +
server_default）、seed 与单测同构数据、逐条执行 admin.py 同款聚合并断言。

用法：POSTGRES_DB=lingxi_pg_smoke POSTGRES_HOST=localhost python scripts/pg_smoke_json_agg.py
跑完自动 DROP 自身数据（清空表），库由调用方删除。
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.message import Message, MessageRole  # noqa: E402
from app.models.session import Session  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

TENANT = "default"
UID = uuid.uuid4()

engine = create_engine(settings.database_url)
print(f"target={settings.database_url.rsplit('@', 1)[-1]}")

# 真 JSONB 列 + server_default（与生产一致；sqlite 测试做不到的部分）
Base.metadata.create_all(
    engine,
    tables=[User.__table__, Session.__table__, Message.__table__],
)

Local = sessionmaker(bind=engine, expire_on_commit=False)
with Local() as db:
    # PG 真实 FK 强制（sqlite 测试 PRAGMA OFF 不触发）：模型无 relationship，
    # 同一 flush 按表名字母序排插入（messages<sessions<users）→ 必炸；
    # 按 FK 链 users→sessions→messages 三段提交
    db.add(User(id=UID, email="smoke@t.local", role=UserRole.user,
                tenant_id=TENANT, password_hash="x"))
    db.commit()
    sid = uuid.uuid4()
    db.add(Session(id=sid, user_id=UID, tenant_id=TENANT))
    db.commit()
    now = datetime.now(UTC).replace(tzinfo=None)  # naive UTC 对齐列语义（同单测口径）
    db.add_all([
        Message(id=uuid.uuid4(), session_id=sid, tenant_id=TENANT,
                role=MessageRole.assistant, content="a1",
                meta={"tool": "order_query"}, created_at=now),
        Message(id=uuid.uuid4(), session_id=sid, tenant_id=TENANT,
                role=MessageRole.assistant, content="a2",
                meta={"tool": "order_query", "first_token_ms": 42.0}, created_at=now),
        Message(id=uuid.uuid4(), session_id=sid, tenant_id=TENANT,
                role=MessageRole.assistant, content="a3",
                meta={"tool": "kb_lookup"}, created_at=now),
        Message(id=uuid.uuid4(), session_id=sid, tenant_id=TENANT,
                role=MessageRole.assistant, content="a4",
                meta={"clarify": True}, created_at=now),
        Message(id=uuid.uuid4(), session_id=sid, tenant_id=TENANT,
                role=MessageRole.assistant, content="a5",
                meta={"clarify": True}, created_at=now),
        Message(id=uuid.uuid4(), session_id=sid, tenant_id=TENANT,
                role=MessageRole.assistant, content="a6", meta={}, created_at=now),
        Message(id=uuid.uuid4(), session_id=sid, tenant_id=TENANT,
                role=MessageRole.user, content="u1",
                meta={"tool": "order_query"}, created_at=now),  # 非 assistant 干扰
    ])
    db.commit()

    # —— admin.py get_admin_stats 同款聚合（PG 方言编译路径）——
    tool_col = Message.meta["tool"].as_string()
    tool_rows = db.execute(
        select(tool_col, func.count(Message.id)).where(
            Message.tenant_id == TENANT,
            Message.role == MessageRole.assistant,
            tool_col.isnot(None),
        ).group_by(tool_col)
    ).all()
    tool_dist = {t: c for t, c in tool_rows if t}
    assert tool_dist == {"order_query": 2, "kb_lookup": 1}, f"tool_dist={tool_dist}"

    clarify_col = Message.meta["clarify"].as_boolean()
    clarify_rounds = db.scalar(
        select(func.count(Message.id)).where(
            Message.tenant_id == TENANT,
            Message.role == MessageRole.assistant,
            clarify_col.is_(True),
        )
    )
    assert clarify_rounds == 2, f"clarify_rounds={clarify_rounds}"

    refuse_count = db.scalar(
        select(func.count(Message.id)).where(
            Message.tenant_id == TENANT,
            Message.role == MessageRole.user,
            Message.intent == "refuse",
        )
    )
    assert refuse_count == 0, f"refuse_count={refuse_count}"

    # —— admin.py get_stats_trend 同款 (created_at, tool) 对查询 ——
    tool_pairs = db.execute(
        select(Message.created_at, tool_col).where(
            Message.tenant_id == TENANT,
            Message.role == MessageRole.assistant,
            tool_col.isnot(None),
        )
    ).all()
    assert len(tool_pairs) == 3, f"tool_pairs={len(tool_pairs)}"
    by_day: dict[str, dict[str, int]] = {}
    for _dt, t in tool_pairs:
        if not t:
            continue
        by_day.setdefault(str(_dt.date()), {})
        by_day[str(_dt.date())][t] = by_day[str(_dt.date())].get(t, 0) + 1
    day = next(iter(by_day))
    assert by_day[day] == {"order_query": 2, "kb_lookup": 1}, f"by_day={by_day}"

    print("PG SMOKE PASS: JSONB ->> 提取 / CAST 布尔 / group_by / (created_at,tool) 对全部正确")

# 清空自造数据（库本身由外部 DROP）
with engine.begin() as conn:
    for t in (Message.__table__, Session.__table__, User.__table__):
        conn.execute(t.delete())
print("cleaned")
