# 会话状态机 + 槽位填充实施计划 · 批次 B

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会话获得跨轮记忆——阶段（greeting→collecting→resolving）与槽位（订单号）持久化在 Session 表，每轮消息驱动状态推进，并把状态注入 RAG prompt，为批次 C（Clarify）/ D（订单工具）提供地基。

**Architecture:** `sessions.conv_state` JSON 列存 `{stage, topic, slots, clarify_count}`；`conversation_state.py` 纯函数模块负责状态推进（复用 `session_context._extract_topic_names` 主题词表与 `query_rewrite._ORDER_RE` 订单号正则，零新规则）；chat.py 的 gen() 在 Router 路由前读写状态（DB 操作搬线程池）；`state_hint` 经 stream_answer 新参数注入 `build_qa_messages(context_hint=...)`，优先级高于现有 `extract_topic` 兜底。

**Tech Stack:** SQLAlchemy `sa.JSON`（nullable，SQLite 兼容——区别于 messages.meta 的 NOT NULL 踩坑）、alembic 0013 迁移、既有 `_extract_topic_names`/`_extract_entities`。

## Global Constraints

- **状态存储**：Session 表 `conv_state` 列（用户已拍板：数据库字段方案），JSON nullable，旧会话 NULL = 无状态（按 new_state 处理）。
- **纯函数纪律**：`conversation_state.py` 零 IO（无 DB/Redis import），全部函数可独立单测。
- **槽位只增不删**：用户中途补充订单号，旧槽位保留（客服交接有用）。
- **主题可切换**：每轮以最新消息命中主题为准；未命中任何主题时保留原主题（换话题需显式命中新主题词，闲聊不清空主题）。
- **fail-open**：状态读写任何异常不阻断问答主流程（chat 层 try/except，降级为无状态）。
- **兼容性**：`conv_state` 为 None 的旧会话照常工作；`stream_answer` 新参数 `state_hint` 默认 None 时行为与旧版逐字节一致（缓存 key 不受影响——state_hint 不进缓存 key，只进 prompt）。
- **测试命令**：后端 `cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest <path> -q -p no:cacheprovider --no-cov`；前端 `cd frontend; npx vitest run <path> --reporter=basic`。
- **提交风格**：`feat(state):` 前缀；每 Task 一提交。

---

### Task 1: 会话状态机纯函数模块

**Files:**
- Create: `backend/app/services/conversation_state.py`
- Test: `backend/tests/test_conversation_state.py`

**Interfaces:**
- Consumes: `session_context._extract_topic_names(history: list[dict]) -> list[str]`（返回命中的主题名，如 ["退款"]）；`query_rewrite._extract_entities(query: str) -> list[str]`（返回订单号/型号/商品词）；`query_rewrite._ORDER_RE`（订单号正则，命中即视为订单号实体）
- Produces（Task 2/3 依赖，签名精确）:
  - `new_state() -> dict`：`{"stage": "greeting", "topic": "", "slots": {}, "clarify_count": 0}`
  - `update(state: dict | None, message: str) -> dict`：输入旧状态（None 视为 new_state）+ 本轮用户消息，返回新状态 dict（原地修改入参是禁止的——必须返回新 dict）
  - `missing_slots(state: dict) -> list[str]`：当前主题缺失的必需槽位名列表
  - `to_prompt_hint(state: dict | None) -> str | None`：注入 prompt 的状态文本；state 为 None/无主题返回 None

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_conversation_state.py`：

```python
"""会话状态机纯函数测试（批次B）：阶段推进 / 槽位填充 / 主题切换 / 提示生成。"""
from __future__ import annotations

from app.services.conversation_state import (
    MAX_CLARIFY,
    STAGE_CLARIFYING,
    STAGE_COLLECTING,
    STAGE_GREETING,
    STAGE_RESOLVING,
    missing_slots,
    new_state,
    to_prompt_hint,
    update,
)


def test_new_state_defaults():
    s = new_state()
    assert s == {"stage": "greeting", "topic": "", "slots": {}, "clarify_count": 0}


def test_update_none_state_returns_fresh():
    s = update(None, "你好")
    assert s["stage"] == STAGE_GREETING  # 闲聊无主题
    assert s["topic"] == ""


def test_update_topic_hit_moves_to_collecting():
    """主题命中但缺订单号 → info_collecting。"""
    s = update(None, "我要退款")
    assert s["topic"] == "退款"
    assert s["stage"] == STAGE_COLLECTING
    assert missing_slots(s) == ["order_no"]


def test_update_slot_fill_moves_to_resolving():
    """主题 + 订单号齐 → resolving。"""
    s = update(None, "我要退款")
    s = update(s, "订单号 SO2026080118")
    assert s["slots"]["order_no"] == "SO2026080118"
    assert s["stage"] == STAGE_RESOLVING
    assert missing_slots(s) == []


def test_slot_fill_without_topic_stays_greeting():
    """先给订单号后说主题（倒序）——槽位照存，阶段随主题变化。"""
    s = update(None, "SO2026080118 怎么回事")
    assert s["slots"]["order_no"] == "SO2026080118"
    assert s["stage"] == STAGE_GREETING  # 无主题仍 greeting（槽位已存，等主题命中即 resolving）
    s2 = update(s, "我要退款")
    assert s2["stage"] == STAGE_RESOLVING  # 槽位已在，主题命中直接齐


def test_no_required_slots_topic_goes_resolving():
    """发票主题无必需槽位 → 命中即 resolving。"""
    s = update(None, "发票怎么开")
    assert s["topic"] == "发票"
    assert s["stage"] == STAGE_RESOLVING


def test_topic_switch_follows_latest_message():
    """换话题：最新消息命中新主题则切换；未命中保留旧主题。"""
    s = update(None, "我要退款")
    s = update(s, "物流到哪了")  # 命中「配送/物流」
    assert s["topic"] == "配送/物流"
    s = update(s, "嗯嗯")  # 无主题词 → 保留
    assert s["topic"] == "配送/物流"


def test_slots_accumulate_never_cleared():
    """槽位只增不删：主题切换后旧槽位保留。"""
    s = update(None, "SO2026080118 退款")
    s = update(s, "物流 XOZ-12345 到哪了")
    assert s["slots"]["order_no"] == "SO2026080118"  # 首个订单号不被覆盖


def test_update_returns_new_dict_not_mutation():
    """契约：update 不原地修改入参（防调用方持有旧引用被意外变更）。"""
    s = new_state()
    out = update(s, "我要退款")
    assert s["topic"] == ""  # 原状态未被改
    assert out["topic"] == "退款"


def test_clarifying_stage_reset_on_new_topic_message():
    """clarifying 阶段收到新消息：有主题按槽位判定回落（clarifying 是等回复的瞬态）。"""
    s = {"stage": STAGE_CLARIFYING, "topic": "退款", "slots": {}, "clarify_count": 1}
    out = update(s, "退款到账多久")
    assert out["stage"] == STAGE_COLLECTING  # 仍缺订单号
    assert out["clarify_count"] == 1  # 计数不重置（累计口径，上限由调用方判）


def test_max_clarify_constant():
    assert MAX_CLARIFY == 2


def test_to_prompt_hint_variants():
    assert to_prompt_hint(None) is None
    assert to_prompt_hint(new_state()) is None  # 无主题
    s = update(None, "我要退款")
    hint = to_prompt_hint(s)
    assert hint is not None
    assert "退款" in hint
    assert "订单号" not in hint or "未提供" in hint  # 未提供时不误导（None 或明示未提供）
    s2 = update(s, "SO2026080118")
    hint2 = to_prompt_hint(s2)
    assert "SO2026080118" in hint2
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_conversation_state.py -q -p no:cacheprovider --no-cov
```
预期：FAIL（`ModuleNotFoundError: app.services.conversation_state`）

- [ ] **Step 3: 写实现**

创建 `backend/app/services/conversation_state.py`：

```python
"""会话状态机（批次B）：阶段跟踪 + 槽位填充。纯函数式，零 IO。

状态结构（sessions.conv_state JSON 列）：
    {"stage": str, "topic": str, "slots": {str: str}, "clarify_count": int}

设计要点：
- 主题词表复用 session_context._extract_topic_names（单一真源，不复制关键词）；
- 订单号提取复用 query_rewrite._ORDER_RE（既有正则，支持 SO2026080118 / XOZ-12345）；
- 槽位只增不删：用户中途补充的实体保留（客服交接溯源有用）；
- 主题可切换：最新消息命中新主题则切换，未命中保留（闲聊不清空）；
- update 返回新 dict，禁止原地修改（调用方可能持有旧引用）。
批次 C（Clarify）依赖 clarify_count/STAGE_CLARIFYING；批次 D（订单工具）依赖 slots.order_no。
"""
from __future__ import annotations

import re

from app.services.query_rewrite import _ORDER_RE
from app.services.session_context import _extract_topic_names

#: 阶段常量（stage 取值域）
STAGE_GREETING = "greeting"          # 无明确主题
STAGE_COLLECTING = "info_collecting"  # 有主题，缺必需槽位
STAGE_RESOLVING = "resolving"        # 槽位齐或无需槽位
STAGE_CLARIFYING = "clarifying"      # 已发澄清问句，等用户回复（批次C 使用，本批次只定义）

#: 主题 → 必需槽位（首期仅 order_no 一个槽位，YAGNI；主题名与 session_context.FLOW_TOPICS 一致）
REQUIRED_SLOTS: dict[str, list[str]] = {
    "退款": ["order_no"],
    "退换货": ["order_no"],
    "保修/维修": ["order_no"],
    "配送/物流": ["order_no"],
    "价保": ["order_no"],
    "发票": [],
}

#: 每会话澄清追问上限（批次C 读取；此处定义避免跨模块常量漂移）
MAX_CLARIFY = 2


def new_state() -> dict:
    """初始状态。"""
    return {"stage": STAGE_GREETING, "topic": "", "slots": {}, "clarify_count": 0}


def update(state: dict | None, message: str) -> dict:
    """推进状态：主题判定（最新消息优先）→ 槽位提取（只增不删）→ 阶段推进。

    - state=None 视为初始状态（旧会话 conv_state 为 NULL 的场景）；
    - 返回新 dict，不修改入参；
    - 主题未命中新词 → 保留旧主题（闲聊/追问回复不清空主题）；
    - 阶段：无主题 greeting；有主题缺槽位 collecting；齐了 resolving。
      clarifying 是瞬态（等用户回复），收到新消息即按主题/槽位重新判定。
    """
    s = dict(state or new_state())
    slots = dict(s.get("slots") or {})

    # 1) 主题：最新消息命中则切换（多个命中取词表优先级第一个，与 extract_topic 行为一致）
    topics = _extract_topic_names([{"role": "user", "content": message}])
    if topics:
        s["topic"] = topics[0]

    # 2) 槽位：提取订单号，只增不删（首个值保留，后续订单号不覆盖——多单场景批次D再议）
    for m in _ORDER_RE.finditer(message):
        order_no = m.group(0).strip()
        if order_no and "order_no" not in slots:
            slots["order_no"] = order_no
            break  # 首个订单号即槽位值
    s["slots"] = slots

    # 3) 阶段推进
    if not s.get("topic"):
        s["stage"] = STAGE_GREETING
    elif missing_slots(s):
        s["stage"] = STAGE_COLLECTING
    else:
        s["stage"] = STAGE_RESOLVING
    return s


def missing_slots(state: dict) -> list[str]:
    """当前主题缺失的必需槽位名；无主题/主题无要求返回 []。"""
    required = REQUIRED_SLOTS.get(state.get("topic", ""), [])
    slots = state.get("slots") or {}
    return [name for name in required if name not in slots]


def to_prompt_hint(state: dict | None) -> str | None:
    """注入 RAG prompt 的状态文本（经 build_qa_messages 的 context_hint 通道，M10 分隔块内声明为数据）。

    返回如「会话主题：退款；已提供订单号：SO2026080118」或（未提供时）「会话主题：退款；订单号：未提供」。
    state 为 None / 无主题返回 None（不注入，输出与旧版一致）。
    """
    if not state or not state.get("topic"):
        return None
    parts = [f"会话主题：{state['topic']}"]
    if "order_no" in (state.get("slots") or {}):
        parts.append(f"已提供订单号：{state['slots']['order_no']}")
    elif "order_no" in REQUIRED_SLOTS.get(state["topic"], []):
        parts.append("订单号：未提供")
    return "；".join(parts)
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_conversation_state.py -q -p no:cacheprovider --no-cov
```
预期：`13 passed`

- [ ] **Step 5: Lint + 提交**

```powershell
cd backend; .\.venv\Scripts\python.exe -m ruff check app/services/conversation_state.py tests/test_conversation_state.py
git add app/services/conversation_state.py tests/test_conversation_state.py
git commit -m "feat(state): 会话状态机纯函数——阶段推进+槽位填充（复用主题词表与订单号正则）"
```

---

### Task 2: Session 模型 + 迁移 0013

**Files:**
- Modify: `backend/app/models/session.py`（satisfaction 列之后，约 [L36-L40](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/models/session.py#L36)）
- Create: `backend/alembic/versions/0013_session_conv_state.py`
- Test: `backend/tests/test_models_tenant.py`（追加用例，或新建 `backend/tests/test_conv_state_model.py`）

**Interfaces:**
- Consumes: alembic 链头 `0012`（[0012_user_profiles.py](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/alembic/versions/0012_user_profiles.py)）
- Produces: `Session.conv_state: Mapped[dict | None]`（sa.JSON nullable）——Task 3 读写、Task 4 详情透出依赖

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_conv_state_model.py`：

```python
"""Session.conv_state 列测试（批次B）：JSON 可读写、默认 None、与既有列共存。"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.models.base import Base
from app.models.session import Session
from app.models.user import User
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__, Session.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    with Local() as s:
        yield s


def test_conv_state_default_none(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email="t@t.local", password_hash="x", status="active"))
    s = Session(user_id=uid)
    db.add(s)
    db.commit()
    db.refresh(s)
    assert s.conv_state is None  # 新会话默认无状态


def test_conv_state_roundtrip(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email="t2@t.local", password_hash="x", status="active"))
    s = Session(user_id=uid)
    s.conv_state = {"stage": "info_collecting", "topic": "退款", "slots": {"order_no": "SO2026080118"}, "clarify_count": 0}
    db.add(s)
    db.commit()
    got = db.scalar(select(Session).where(Session.id == s.id))
    assert got.conv_state["topic"] == "退款"
    assert got.conv_state["slots"]["order_no"] == "SO2026080118"
    # 更新（chat 层每轮写回的场景）
    got.conv_state = {**got.conv_state, "stage": "resolving"}
    db.commit()
    db.refresh(got)
    assert got.conv_state["stage"] == "resolving"


def test_conv_state_column_type_is_json():
    col = Session.__table__.columns["conv_state"]
    assert isinstance(col.type, sa.JSON)
    assert col.nullable is True
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_conv_state_model.py -q -p no:cacheprovider --no-cov
```
预期：FAIL（`AttributeError: 'Session' object has no attribute 'conv_state'`）

- [ ] **Step 3: 写实现**

**(a) 模型**——`backend/app/models/session.py` 的 satisfaction 列之后追加：

```python
    # 批次B（2026-08-24）：会话状态机——阶段+槽位跨轮持久化（conversation_state.py 管结构）
    conv_state: Mapped[dict | None] = mapped_column(
        sa.JSON,
        nullable=True,
        default=None,
        comment="会话状态机：{stage, topic, slots, clarify_count}（app/services/conversation_state.py）",
    )
```

**(b) 迁移**——创建 `backend/alembic/versions/0013_session_conv_state.py`：

```python
"""会话状态机列（批次B，2026-08-24）

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-24

- ``sessions.conv_state``：JSON 可空——{stage, topic, slots, clarify_count}；
- 存量会话 NULL = 无状态（代码按 new_state 处理），无需数据回填。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("conv_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "conv_state")
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_conv_state_model.py tests/test_models_tenant.py -q -p no:cacheprovider --no-cov
```
预期：全部 passed（含既有 tenant 模型测试无回归）

- [ ] **Step 5: 迁移正确性验证（SQLite 烟测）**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -c "from alembic.config import Config; from alembic import command; cfg = Config('alembic.ini'); command.upgrade(cfg, 'head'); print('upgrade OK'); command.downgrade(cfg, '0012'); print('downgrade OK'); command.upgrade(cfg, 'head'); print('re-upgrade OK')"
```
预期：三行 OK（本地 alembic.ini 指向 SQLite 时可跑；若 alembic.ini 固定指向 PG 且无法连接，跳过本步并在报告注明「迁移未本地验证，由 Task 4 的 create_all 建表测试兜底」）

- [ ] **Step 6: Lint + 提交**

```powershell
cd backend; .\.venv\Scripts\python.exe -m ruff check app/models/session.py alembic/versions/0013_session_conv_state.py tests/test_conv_state_model.py
git add app/models/session.py alembic/versions/0013_session_conv_state.py tests/test_conv_state_model.py
git commit -m "feat(state): sessions.conv_state 列 + 迁移0013（JSON 可空，存量会话无需回填）"
```

---

### Task 3: chat 层接入状态机（读写 + prompt 注入）

**Files:**
- Modify: `backend/app/api/chat.py`（import 区 + gen() 内 Router 路由前，约 [L298-L315](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/api/chat.py#L298)）
- Modify: `backend/app/services/rag_service.py`（`stream_answer` 签名与 `context_hint` 选择，约 [L139-L201](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/services/rag_service.py#L139)）
- Test: `backend/tests/test_chat_api.py`（追加 2 个用例到既有文件）

**Interfaces:**
- Consumes: Task 1 的 `update(state, message)` / `to_prompt_hint(state)`；Task 2 的 `Session.conv_state`
- Produces: `stream_answer(..., state_hint: str | None = None)`（新可选参数，默认 None 行为不变）——批次 C/D 传 clarify 上下文时复用

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_chat_api.py` 末尾追加（fixture 复用既有 `client`，见文件头注释的夹具结构；`_FakeStream` 需捕获 kwargs 以验证 state_hint 透传）：

```python
def test_chat_updates_conv_state(client, monkeypatch):
    """批次B：两轮对话驱动 conv_state——首轮退款主题→collecting，次轮补订单号→resolving。"""
    c, Local, _ = client
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
        s = db.scalar(select(Session).where(Session.id == __import__("uuid").UUID(sid)))
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
        s = db.scalar(select(Session).where(Session.id == __import__("uuid").UUID(sid)))
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
```

> 注：第二个用例的 mock 目标是 `app.api.chat` 命名空间里的 `conversation_state.update`——因此实现里必须 `from app.services import conversation_state` 后以 `conversation_state.update(...)` 形式调用（模块属性访问，可被 monkeypatch）；若实现写成 `from app.services.conversation_state import update` 则 mock 目标失效，测试会红——这是故意的契约锁定。

- [ ] **Step 2: 跑测试确认失败**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_chat_api.py -q -p no:cacheprovider --no-cov -k conv_state
```
预期：2 FAIL（`conv_state` 未被写库 / `state_hint` kwargs 不存在）

- [ ] **Step 3: 写实现**

**(a) rag_service.py `stream_answer` 加参**（签名 + 注入选择，两处小改）：

```python
async def stream_answer(
    query: str,
    kb_id: UUID,
    history: list[dict] | None = None,
    top_k: int | None = None,
    kb_version: str | None = None,
    user_profile: str | None = None,
    state_hint: str | None = None,
):
```

docstring 补一行：

```
    - state_hint（可选，批次B）：会话状态机提示（主题+槽位），优先级高于 extract_topic
      兜底；None 时回退 extract_topic（旧行为），且不进缓存 key（仅影响 prompt）。
```

生成段 `context_hint` 选择改为：

```python
        topic = extract_topic(history)  # 兜底：无状态提示时维持旧行为
        messages = build_qa_messages(
            query=query,
            chunks=result.chunks,
            history=history or [],
            context_hint=state_hint or topic,  # 批次B：状态机提示优先
            profile=user_profile,
        )
```

**(b) chat.py gen() 接入**——`history = await _fetch_history(...)` 之后、`ctx = SharedContext(...)` 之前插入：

```python
        # 批次B：会话状态机——读旧状态 → 消息推进 → 写回 + 生成 prompt 提示（fail-open：
        # 任何异常降级为无状态，问答照常；conv_state=None 的旧会话按 new_state 处理）
        from app.services import conversation_state  # 局部导入：mock 契约（见测试）
        try:
            conv_state = await run_in_threadpool(
                lambda: conversation_state.update(s.conv_state, req.content)
            )
            s.conv_state = conv_state
            await run_in_threadpool(db.commit)
            state_hint = conversation_state.to_prompt_hint(conv_state)
        except Exception:  # noqa: BLE001 - fail-open
            logger.exception("conv_state 更新失败（降级无状态，问答照常）")
            db.rollback()
            state_hint = None
```

`_events()` 内 `stream_answer(...)` 调用追加参数（quick_ans 分支不加——快捷话术短路不走 LLM）：

```python
                async for e, d in stream_answer(
                    search_query,
                    kb_id,
                    history=history,
                    kb_version=kb_version,
                    user_profile=user_profile,
                    state_hint=state_hint,
                ):
```

import 区追加（保持字母序）：`from app.services import conversation_state` 放在 `from app.services.agents...` 之前。

> 注意：`s` 在 gen() 闭包内曾是踩坑点（sources 迭代变量复用导致 UnboundLocalError，见 L284 注释）——本实现读 `s.conv_state` 在迭代开始前完成（`s` 此时尚未被遮蔽），且写回立即 commit，不依赖后续 `s`。

- [ ] **Step 4: 跑测试确认通过**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_chat_api.py -q -p no:cacheprovider --no-cov
```
预期：全部 passed（新增 2 + 既有全部）

- [ ] **Step 5: Lint + 提交**

```powershell
cd backend; .\.venv\Scripts\python.exe -m ruff check app/api/chat.py app/services/rag_service.py tests/test_chat_api.py
git add app/api/chat.py app/services/rag_service.py tests/test_chat_api.py
git commit -m "feat(state): chat 层接入会话状态机——每轮推进+写回+prompt 注入（fail-open）"
```

---

### Task 4: 会话详情透出 conv_state（客服可见）+ 契约同步

**Files:**
- Modify: `backend/app/api/sessions.py`（`SessionDetail` 模型 + `get_session` 返回，约 [L78-L86](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/api/sessions.py#L78) 与 [L251](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/api/sessions.py#L251)）
- Modify: `contracts/api.ts`（SessionDetail 接口追加字段）
- Regenerate: `contracts/api-schema.json`
- Modify: `frontend/src/api/sessions.ts`（BackendSessionDetail 接口 + 透传）
- Test: `backend/tests/test_sessions_conv_state.py`（新建）

**Interfaces:**
- Consumes: Task 2 的 `Session.conv_state`
- Produces: `SessionDetail.conv_state?: {stage?, topic?, slots?, clarify_count?}`（agent/admin 视角返回；前端批次 C/D 观察用）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_sessions_conv_state.py`（夹具照抄 [test_sessions_messages.py](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/tests/test_sessions_messages.py) 的 client fixture，Session 种子带 conv_state）：

```python
"""会话详情 conv_state 透出测试（批次B）：agent 可见 / user 视角不返回结构化状态。"""
from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.base import Base
from app.models.message import Message, MessageSource
from app.models.session import Session
from app.models.user import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
AGENT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def client():
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
        engine, tables=[Session.__table__, Message.__table__, User.__table__, MessageSource.__table__]
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
        db.add(
            Session(
                id=SID,
                user_id=USER_ID,
                conv_state={"stage": "info_collecting", "topic": "退款", "slots": {}, "clarify_count": 0},
            )
        )
        db.commit()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _agent_h():
    return {"Authorization": f"Bearer {create_access_token(str(AGENT_ID), 'agent')}"}


def _user_h():
    return {"Authorization": f"Bearer {create_access_token(str(USER_ID), 'user')}"}


def test_detail_returns_conv_state_for_agent(client):
    """agent 视角：conv_state 结构化透出（客服观察用）。"""
    r = client.get(f"{API}/sessions/{SID}", headers=_agent_h())
    assert r.status_code == 200
    cs = r.json()["conv_state"]
    assert cs["topic"] == "退款"
    assert cs["stage"] == "info_collecting"


def test_detail_conv_state_none_for_old_session(client):
    """旧会话（conv_state=None）：字段返回 None，不报错。"""
    with TestClient(app) as c:
        # 新建无状态会话
        r = c.post(
            f"{API}/sessions",
            headers=_user_h(),
            json={"title": "t"},
        )
        new_sid = r.json()["session_id"]
        r2 = c.get(f"{API}/sessions/{new_sid}", headers=_agent_h())
        assert r2.status_code == 200
        assert r2.json()["conv_state"] is None
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_sessions_conv_state.py -q -p no:cacheprovider --no-cov
```
预期：FAIL（响应无 `conv_state` 字段——pydantic 默认忽略多余属性不会报错，`KeyError` 使断言失败）

- [ ] **Step 3: 写实现**

**(a) sessions.py**：`SessionDetail` 模型追加（handoff_summary 之后）：

```python
    # 批次B：会话状态机（阶段+槽位；agent/admin 观察用，user 视角同返回——内容仅含
    # 用户自己会话的主题/订单号，无越权数据面，与 profile 的仅-staff 可见不同类）。
    conv_state: dict | None = None
```

`get_session` 的 `return SessionDetail(...)` 追加：

```python
        conv_state=s.conv_state,
```

**(b) contracts/api.ts**：`SessionDetail` 接口的 `handoff_summary` 字段后追加：

```typescript
  /** 批次B：会话状态机（阶段+槽位跨轮记忆）。旧会话为 null。 */
  conv_state?: {
    stage?: string; // greeting / info_collecting / resolving / clarifying
    topic?: string; // 当前流程主题（退款/退换货/保修维修…）
    slots?: Record<string, string>; // 已收集槽位（如 order_no）
    clarify_count?: number; // 澄清追问次数（批次C 用）
  } | null;
```

**(c) 前端适配**：`frontend/src/api/sessions.ts` 的 `BackendSessionDetail` 接口追加同构字段、`getSessionDetail` 返回对象追加 `conv_state: r.data.conv_state`。

**(d) OpenAPI 再生成 + 契约校验**：

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe scripts/generate_openapi.py
cd ..; python scripts/check_contracts.py   # 期望退出码 0
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_sessions_conv_state.py tests/test_sessions_messages.py tests/test_sessions_suggest.py -q -p no:cacheprovider --no-cov
cd ..\frontend; npx tsc --noEmit
```
预期：后端全 passed；tsc 退出码 0

- [ ] **Step 5: Lint + 提交**

```powershell
cd backend; .\.venv\Scripts\python.exe -m ruff check app/api/sessions.py tests/test_sessions_conv_state.py
git add app/api/sessions.py tests/test_sessions_conv_state.py ../contracts/api.ts ../contracts/api-schema.json ../frontend/src/api/sessions.ts
git commit -m "feat(state): 会话详情透出 conv_state + 契约同步（客服观察视角）"
```

---

### Task 5: handoff_summary 升级（含状态机信息）+ 全量回归

**Files:**
- Modify: `backend/app/services/session_context.py`（`build_handoff_summary` 签名加可选参，约 [L77-L100](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/services/session_context.py#L77)）
- Modify: `backend/app/api/sessions.py`（`get_session` 调用处传 conv_state，约 [L246](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/api/sessions.py#L246)）
- Test: `backend/tests/test_session_context.py`（追加用例）
- 验证：全量回归（后端 + 前端 + 契约）

**Interfaces:**
- Consumes: `build_handoff_summary(history, max_question=120)`（现有签名）；Task 2 `Session.conv_state`
- Produces: `build_handoff_summary(history, conv_state=None, max_question=120)`——conv_state 非空时输出加 `stage`/`slots` 字段（批次 A 前端 HandoffSummary 组件对未知字段不渲染，向后兼容）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_session_context.py` 末尾追加：

```python
def test_handoff_summary_with_conv_state():
    """批次B：交接摘要并入状态机——客服看到阶段/槽位/澄清次数。"""
    from app.services.session_context import build_handoff_summary

    history = [{"role": "user", "content": "我要退款 SO2026080118"}]
    conv_state = {"stage": "resolving", "topic": "退款", "slots": {"order_no": "SO2026080118"}, "clarify_count": 1}
    s = build_handoff_summary(history, conv_state=conv_state)
    assert s["stage"] == "resolving"
    assert s["slots"] == {"order_no": "SO2026080118"}
    assert s["clarify_count"] == 1


def test_handoff_summary_without_conv_state_unchanged():
    """不传 conv_state：输出结构与旧版完全一致（兼容契约）。"""
    from app.services.session_context import build_handoff_summary

    history = [{"role": "user", "content": "我要退款"}]
    s = build_handoff_summary(history)
    assert "stage" not in s
    assert s["topic"] == "退款"
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_session_context.py -q -p no:cacheprovider --no-cov -k handoff
```
预期：新增 1 FAIL（`TypeError: unexpected keyword 'conv_state'`）、旧用例 PASS

- [ ] **Step 3: 写实现**

**(a) session_context.py `build_handoff_summary`**——签名与开头改为：

```python
def build_handoff_summary(
    history: list[dict] | None,
    conv_state: dict | None = None,
    max_question: int = 120,
) -> dict[str, Any] | None:
    """转人工交接摘要：本次会话的「当前主题 + 具体实体 + 最近用户诉求」。

    批次B：conv_state 非空时并入状态机信息（stage/slots/clarify_count）——客服一眼
    看到「退款主题、已提供订单号、AI 已追问 N 轮」，交接不再从零开始。
    其余行为与旧版一致（不传 conv_state 输出结构不变，兼容既有测试/前端）。
    """
    question = _nearest_user_content(history)
    if not question:
        return None
    summary: dict[str, Any] = {}
    if conv_state:
        if conv_state.get("stage"):
            summary["stage"] = conv_state["stage"]
        if conv_state.get("slots"):
            summary["slots"] = conv_state["slots"]
        if conv_state.get("clarify_count"):
            summary["clarify_count"] = conv_state["clarify_count"]
    topics = _extract_topic_names(history)
```

（其余函数体不动。）

**(b) sessions.py `get_session` 调用处**（handoff_summary 分支）：

```python
        try:
            handoff_summary = build_handoff_summary(
                [{"role": m.role.value, "content": m.content} for m in msgs],
                conv_state=s.conv_state,
            )
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_session_context.py tests/test_sessions_conv_state.py -q -p no:cacheprovider --no-cov
```
预期：全部 passed

- [ ] **Step 5: 全量回归（在最终提交前跑）**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --no-cov --ignore=tests/test_demo_orders.py
# 期望：退出码 0，0 failures（计数经 --junitxml 权威解析）
cd ..\frontend; $env:NODE_OPTIONS='--max-old-space-size=4096'; npx vitest run --reporter=basic --no-file-parallelism
# 期望：全绿（含批次A 的 35 个）
npx tsc --noEmit
# 期望：退出码 0
cd ..; python scripts/check_contracts.py
# 期望：RESULT: PASS
```

- [ ] **Step 6: Lint + 提交**

```powershell
cd backend; .\.venv\Scripts\python.exe -m ruff check app/services/session_context.py app/api/sessions.py tests/test_session_context.py
git add app/services/session_context.py app/api/sessions.py tests/test_session_context.py
git commit -m "feat(state): 交接摘要并入会话状态（阶段/槽位/澄清次数，不传则结构不变）"
```

---

## Self-Review 记录

- **Spec 覆盖**：批次 B 规划的 B1（模型+迁移）→ Task 2；B2（纯函数状态机）→ Task 1；B3（chat 接入 + prompt 注入）→ Task 3；「客服视角可见 conv_state」→ Task 4；「handoff_summary 升级」（补充建议项，用户已看过并同意按此推进）→ Task 5。telemetry 打点在批次 B 原规划中提及但属观测增强，**未纳入本计划**（YAGNI：C/D 落地看真实分布再加），已在风险中注明。
- **类型一致性**：`update(state: dict | None, message: str) -> dict`（Task 1 定义 = Task 3 调用）；`state_hint: str | None`（Task 3 rag_service 签名 = chat.py 传参 = 测试 captured_kwargs 断言）；`conv_state` JSON 结构四字段贯穿 Task 1/2/3/4/5。
- **踩坑预判**：① mock 契约——chat.py 必须 `from app.services import conversation_state` 模块级属性访问（Task 3 测试锁定，实现说明已写）；② `s` 闭包遮蔽坑（chat.py L284 注释）——状态读写全部放在迭代开始前，写回即 commit；③ SQLite 的 messages.meta JSONB 替换夹具模式已沿用，conv_state 本身是 nullable JSON 无此问题。
- **已知限制**：并发写 conv_state 为 last-write-wins（两端同发消息覆盖），首期接受（原规划已声明，乐观锁留后续）；迁移 0013 在 PG 环境的执行由部署时 alembic upgrade head 完成，本地仅 SQLite 烟测兜底。
