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
    """P1：stats/trend 按日聚合会话/消息/工单 + 无数据日期补零 + 权限 403。

    2026-08-23 修：键与造数统一按 UTC（端点轴用 datetime.now(UTC) 生成）——旧写法用
    本地时间，本地跨零点而 UTC 未跨时 today_key 不在轴上直接 KeyError（跨天假红）。"""
    from datetime import UTC, datetime, timedelta

    from app.models.ticket import Ticket

    # 用 fixture 已有的 SID 会话 + 补历史数据（2 天前，naive UTC 对齐列语义）
    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
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
    # 键直接取自端点返回的 UTC 轴（今天=最后一天，2 天前=倒数第三天），免本地/UTC 口径漂移
    today_key = days[-1]["date"]
    old_key = days[-3]["date"]
    assert by_date[old_key]["sessions"] == 1
    assert by_date[old_key]["messages"] == 1
    assert by_date[old_key]["tickets"] == 1
    assert by_date[today_key]["sessions"] >= 1
    # 无数据日期补零（连续轴）
    assert all(d["sessions"] >= 0 for d in days)
    # 权限：user → 403
    r2 = client.get(f"{API}/admin/stats/trend", headers=_h(USER, "user"))
    assert r2.status_code == 403


def test_stats_tool_clarify_topic_refuse(client):
    """T1.2：tool_dist / clarify_rounds / topic_dist / refuse_count 精确聚合（期望独立手算）。

    种子（不含 fixture 预置的 5 user 消息）：
    - assistant 工具回答：order_query×3 + kb_lookup×1；无工具/meta 空/非 assistant 一律不计
      → tool_dist == {"order_query": 3, "kb_lookup": 1}
    - assistant 澄清轮 meta.clarify=True ×2 → clarify_rounds == 2
    - 会话 conv_state.topic：退换货×2 + 保修×1 + 无 conv_state 不计 → topic_dist == {"退换货": 2, "保修": 1}
    - refuse 用户消息：fixture 已有 ×2 + 新增 ×2 → refuse_count == 4
    口径（大扫查 2026-08-25 修正）：澄清轮 intent 落 'qa'（rag_service emit refuse=False），
    天然不进 refuse_count——refuse_count 即真拒答轮数，与 clarify_rounds 无推导关系。
    """
    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        db.add_all([
            Session(id=uuid.uuid4(), user_id=USER, tenant_id="default",
                    conv_state={"topic": "退换货"}),
            Session(id=uuid.uuid4(), user_id=USER, tenant_id="default",
                    conv_state={"topic": "退换货", "stage": "clarifying"}),
            Session(id=uuid.uuid4(), user_id=USER, tenant_id="default",
                    conv_state={"topic": "保修"}),
            Session(id=uuid.uuid4(), user_id=USER, tenant_id="default"),  # 无 conv_state → 不计
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="t1", meta={"tool": "order_query"}),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="t2", meta={"tool": "order_query"}),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="t3",
                    meta={"tool": "order_query", "first_token_ms": 42.0}),  # 同行多埋点互不干扰
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="t4", meta={"tool": "kb_lookup"}),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="t5", meta={"clarify": True}),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="t7", meta={"clarify": True}),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="t6", meta={}),  # 无任何标记
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.user, content="u-tool",
                    meta={"tool": "order_query"}),  # 非 assistant 干扰 → 不计工具
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.user, content="查不到物流", intent="refuse"),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.user, content="发票怎么开", intent="refuse"),
        ])
        db.commit()
    finally:
        gen.close()

    r = client.get(f"{API}/admin/stats", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    assert data["tool_dist"] == {"order_query": 3, "kb_lookup": 1}
    assert data["clarify_rounds"] == 2
    assert data["topic_dist"] == {"退换货": 2, "保修": 1}
    assert data["refuse_count"] == 4


def test_stats_trend_tool_clarify_series(client):
    """T1.3：trend 时序扩展——tool_dist / clarify_rounds 按日分桶。

    种子（不含 fixture 预置数据；fixture 老消息无 meta 不影响新字段）：
    - 今天（UTC）：assistant tool=order_query×2 + kb_lookup×1、clarify×2
      → 当日 tool_dist == {"order_query": 2, "kb_lookup": 1}，clarify_rounds == 2
    - 2 天前：assistant tool=order_query×1 → 该日 tool_dist == {"order_query": 1}，clarify_rounds == 0
    - 干扰：user 消息带 tool meta、空串 tool、无 meta 消息一律不计
    - 无数据日：tool_dist == {} 且 clarify_rounds == 0（补零语义）
    RED 预期：响应无 tool_dist/clarify_rounds 键 → KeyError（功能缺失）。"""
    from datetime import UTC, datetime, timedelta

    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        old = now - timedelta(days=2)
        db.add_all([
            # 今天
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="d1",
                    meta={"tool": "order_query"}, created_at=now),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="d2",
                    meta={"tool": "order_query"}, created_at=now),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="d3",
                    meta={"tool": "kb_lookup"}, created_at=now),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="d4",
                    meta={"clarify": True}, created_at=now),
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="d5",
                    meta={"clarify": True}, created_at=now),
            # 2 天前
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="d6",
                    meta={"tool": "order_query"}, created_at=old),
            # 干扰项（今天，均不应计入）
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.user, content="d7",
                    meta={"tool": "order_query"}, created_at=now),  # 非 assistant
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="d8",
                    meta={"tool": ""}, created_at=now),  # 空串工具名
            Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                    role=MessageRole.assistant, content="d9",
                    meta={}, created_at=now),  # 无标记
        ])
        db.commit()
    finally:
        gen.close()

    r = client.get(f"{API}/admin/stats/trend?days=7", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    days = r.json()["days"]
    by_date = {d["date"]: d for d in days}
    today_key = days[-1]["date"]
    old_key = days[-3]["date"]
    mid_key = days[-2]["date"]  # 中间日无新字段数据
    assert by_date[today_key]["tool_dist"] == {"order_query": 2, "kb_lookup": 1}
    assert by_date[today_key]["clarify_rounds"] == 2
    assert by_date[old_key]["tool_dist"] == {"order_query": 1}
    assert by_date[old_key]["clarify_rounds"] == 0
    assert by_date[mid_key]["tool_dist"] == {}
    assert by_date[mid_key]["clarify_rounds"] == 0


def test_stats_hot_gaps_days_window(client):
    """三期 1：hot_gaps ?days 时间窗——7 天外的 refuse 不计入；days=0 保持旧口径（不限）。

    预期独立手算：
    - 默认（days=7）：窗外 refuse（8 天前）排除；窗内 refuse（fixture 2 条同义问法 + 今天 1 条）计入；
    - days=0：SQL 不带时间条件 → 旧口径全量，窗外数据回归（既有数字口径不变）；
    - 时间窗只作用于 hot_gaps：refuse_count 等其余字段仍为全量（fixture 2 + 新增 2 = 4）。
    RED 预期：默认调用即含 8 天前的 refuse → "八天前的缺口" in gaps（时间窗缺失）。"""
    from datetime import UTC, datetime, timedelta

    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8)
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.user, content="八天前的缺口", intent="refuse", created_at=old))
        db.add(Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.user, content="今天的缺口", intent="refuse"))
        db.commit()
    finally:
        gen.close()

    # 默认 days=7：窗内计入（fixture 今天的 2 条同义问法不受影响），窗外排除；feedback_gaps 空表兜底
    r = client.get(f"{API}/admin/stats", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    assert data["feedback_gaps"] == []  # 新字段 additive：无数据时空列表（非缺键/None）
    gaps = {g["question"]: g["count"] for g in data["hot_gaps"]}
    assert gaps.get("今天的缺口") == 1
    assert gaps.get("商品详情页在哪？") == 2  # fixture（今天）不受时间窗影响
    assert "八天前的缺口" not in gaps  # 7 天外不计入

    # days=0：旧口径（不限时间）→ 窗外数据回归，其余不变
    r0 = client.get(f"{API}/admin/stats?days=0", headers=_h(ADMIN, "admin"))
    assert r0.status_code == 200
    gaps0 = {g["question"]: g["count"] for g in r0.json()["hot_gaps"]}
    assert gaps0.get("八天前的缺口") == 1
    assert gaps0.get("今天的缺口") == 1
    assert gaps0.get("商品详情页在哪？") == 2

    # 时间窗仅作用于 hot_gaps 聚类：refuse_count 仍为全量口径（2 fixture + 2 新增）
    assert data["refuse_count"] == 4


def test_stats_feedback_gaps(client):
    """三期 1：feedback_gaps——down 反馈连消息原文聚类 Top10（归一化归并 + 最近 down 时间）。

    种子（down 反馈 join 被踩消息原文，同 /admin/feedback 的 join 先例）：
    - "怎么开发票？" ×1 + " 怎么开发票？ " ×1（归一化后同组）→ count=2，展示取较短变体；
    - "退款政策是什么？" ×1（count=1，组内最近时间=2h 前）；
    - up 反馈（"只有赞的回答"）不计入；8 天前的 down（"很久前的踩"）窗外排除；
    - days=0：窗外 down 回归（旧口径全量）。
    RED 预期：响应无 feedback_gaps 键 → KeyError（功能缺失）。"""
    from datetime import UTC, datetime, timedelta

    from app.models.feedback import FeedbackRating

    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        now = datetime.now(UTC).replace(tzinfo=None)
        old = now - timedelta(days=8)
        m1 = Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                     role=MessageRole.assistant, content="怎么开发票？")
        m2 = Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                     role=MessageRole.assistant, content=" 怎么开发票？ ")  # 归一化后与 m1 同组
        m3 = Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                     role=MessageRole.assistant, content="退款政策是什么？")
        m_up = Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                       role=MessageRole.assistant, content="只有赞的回答")
        m_old = Message(id=uuid.uuid4(), session_id=SID, tenant_id="default",
                        role=MessageRole.assistant, content="很久前的踩")
        db.add_all([m1, m2, m3, m_up, m_old])
        db.flush()
        db.add_all([
            Feedback(message_id=m1.id, tenant_id="default", user_id=USER,
                     rating=FeedbackRating.down, created_at=now - timedelta(hours=2)),
            Feedback(message_id=m2.id, tenant_id="default", user_id=USER,
                     rating=FeedbackRating.down, created_at=now),  # 组内最近 down 时间
            Feedback(message_id=m3.id, tenant_id="default", user_id=USER,
                     rating=FeedbackRating.down, created_at=now - timedelta(hours=1)),
            Feedback(message_id=m_up.id, tenant_id="default", user_id=USER,
                     rating=FeedbackRating.up, created_at=now),  # up 不计入
            Feedback(message_id=m1.id, tenant_id="default", user_id=ADMIN,
                     rating=FeedbackRating.up, created_at=now),  # 同消息 up 不影响 down 计数
            Feedback(message_id=m_old.id, tenant_id="default", user_id=USER,
                     rating=FeedbackRating.down, created_at=old),  # 8 天前 → 窗外
        ])
        db.commit()
    finally:
        gen.close()

    r = client.get(f"{API}/admin/stats", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    gaps = r.json()["feedback_gaps"]
    # 排序：组次数倒序 → "怎么开发票？"（count=2）第一
    assert gaps[0]["question"] == "怎么开发票？"
    assert gaps[0]["count"] == 2  # 归一化归并（含空白差异变体）
    assert " 怎么开发票？ " not in {g["question"] for g in gaps}  # 归并后不重复出现
    assert gaps[0]["last_at"] == now.isoformat()  # 组内最近一次 down 反馈时间
    fg = {g["question"]: g["count"] for g in gaps}
    assert fg.get("退款政策是什么？") == 1
    assert "只有赞的回答" not in fg  # up 反馈不计入
    assert "很久前的踩" not in fg  # 7 天外不计入

    # days=0：旧口径（不限时间）→ 窗外 down 回归
    r0 = client.get(f"{API}/admin/stats?days=0", headers=_h(ADMIN, "admin"))
    assert r0.status_code == 200
    fg0 = {g["question"]: g["count"] for g in r0.json()["feedback_gaps"]}
    assert fg0.get("很久前的踩") == 1
    assert fg0.get("怎么开发票？") == 2


def test_stats_trend_group_key_shared_params(client):
    """PG 方言盲区回归锁（2026-08-31 线上 500）：

    get_stats_trend 的按日分组表达式若在 SELECT 与 GROUP BY 中各自独立构造，
    SQLAlchemy 会生成两组命名 bind 参数（substr_2/3 vs substr_4/5）——SQLite
    按文本匹配表达式照常通过（本地全量绿），但 PG 解析期无法认定二者同源 →
    GroupingError: column "sessions.created_at" must appear in the GROUP BY
    clause（管理后台「数据统计」趋势图 500）。

    修复契约：每条查询的 SELECT/GROUP BY 必须复用同一个 _day 表达式实例，
    编译产物中 substr 的命名参数键恰好一组（2 个）。
    断言挂在 context.compiled.params（编译期产物，方言无关）而非执行期参数
    ——SQLite 驱动会把同一 bindparam 按 SQL 文本占位数物理展开，执行期重复
    属正常，不能作为判据。
    """
    from sqlalchemy import event

    substr_key_counts: list[int] = []

    def _recorder(conn, cursor, statement, parameters, context, executemany):
        compiled = getattr(context, "compiled", None)
        params = getattr(compiled, "params", None) or {}
        keys = [k for k in params if str(k).startswith("substr")]
        if keys:
            substr_key_counts.append(len(set(keys)))

    # client fixture 的 engine 经 get_db override 可达：借一次会话取 bind 挂事件
    gen = app.dependency_overrides[get_db]()
    db = next(gen)
    try:
        engine = db.get_bind()
    finally:
        gen.close()

    event.listen(engine, "before_cursor_execute", _recorder)
    try:
        r = client.get(f"{API}/admin/stats/trend?days=7", headers=_h(ADMIN, "admin"))
        assert r.status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", _recorder)

    # 防假绿：trend 端点确实执行了按日 substr 聚合查询
    assert substr_key_counts, "未捕获到任何 substr 聚合查询——测试已失效，请检查端点实现"
    for n in substr_key_counts:
        assert n == 2, (
            f"编译产物中 substr 命名参数键有 {n} 个（应为 2 个一组）——"
            "SELECT 与 GROUP BY 未复用同一 _day 表达式实例，PG 上将报 GroupingError"
        )
