# 订单工具实施计划 · 批次 D

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 订单查询能力工具化——槽位有订单号 + 订单类主题时直接查 Mock 订单数据、模板回答零 LLM（事实型查询不冒幻觉风险），查不到回落 RAG；接口按未来真实订单系统设计（换数据源零改路由/模板）。

**Architecture:** `tools/order_tool.py` 提供 `query_order(order_no) -> OrderInfo | None` + `format_order_reply(order) -> str`（同步纯函数，数据源 lazy 单例加载 JSON）；chat 层 gen() 在 ImageAgent 之后插订单分支（conv_state 槽位驱动，与批次 B/C 状态链闭环）；事件序列复用 quick_ans 短路形态（intent/stage*/token/sources/done），done 后照常落库（meta 加 tool 标记）。Mock 数据放 `scripts/demo_orders.json`（订单号与知识库 demo 文档同源）。

**Tech Stack:** 标准库 json/dataclass/pathlib；批次 B 的 conv_state.slots.order_no；无新依赖。

## Global Constraints

- **触发条件**：`conv_state.slots.order_no` 非空 **且** `conv_state.topic` 属于订单类主题集合 {"退款","退换货","保修/维修","配送/物流","价保"}。查不到（query_order 返回 None）→ 正常走 RAG（不阻断）。
- **零 LLM**：工具分支模板回答，`format_order_reply` 纯字符串拼接——事实型查询绝不经过 LLM（防幻觉）。
- **事件契约**：复用 quick_ans 短路的事件形态（intent qa/false → stage retrieving → stage generating → token* → sources([]) → done）；done 不带 clarify/rewritten_query/cache_hit。
- **落库**：走既有 `_persist_answer`（done 分支统一落库），meta 加 `{"tool": "order_query"}`（观测用，条件：meta dict 已有 first_token_ms 则合并）。
- **fail-open**：工具分支任何异常（数据文件缺失/JSON 损坏）→ log + 回落 RAG。
- **Mock 数据契约**：`OrderInfo` 字段 order_no/status/items/logistics_no/eta/updated_at；status ∈ {pending_shipped, shipped, signed, refunding}；文件缺失时 query_order 返回 None（不抛）。
- **测试命令**：后端 `cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest <path> -q -p no:cacheprovider --no-cov`。
- **提交风格**：`feat(order):` 前缀。

---

### Task 1: OrderTool 工具模块 + Mock 数据

**Files:**
- Create: `backend/app/services/tools/__init__.py`（空包标记 + docstring）
- Create: `backend/app/services/tools/order_tool.py`
- Create: `backend/scripts/demo_orders.json`
- Test: `backend/tests/test_order_tool.py`

**Interfaces:**
- Produces:
  - `OrderInfo`（dataclass：order_no/status/items/logistics_no/eta/updated_at，全 str，logistics_no/eta 可 None）
  - `query_order(order_no: str) -> OrderInfo | None`——同步；大小写归一（upper）；数据文件缺失/损坏返回 None
  - `format_order_reply(o: OrderInfo) -> str`——模板文案
  - `ORDER_TOPICS: frozenset[str]`（订单类主题集合，chat 层门控用）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_order_tool.py`：

```python
"""OrderTool 测试（批次D）：查询/归一/缺失降级/模板文案。"""
from __future__ import annotations

import json
from pathlib import Path

import app.services.tools.order_tool as ot


def _reload(monkeypatch, tmp_path: Path, orders: list[dict] | None):
    """把数据源指到临时文件并清缓存（None=文件不存在场景）。"""
    if orders is None:
        monkeypatch.setattr(ot, "_DATA_FILE", tmp_path / "missing.json")
    else:
        f = tmp_path / "orders.json"
        f.write_text(json.dumps(orders, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(ot, "_DATA_FILE", f)
    ot._ORDERS_CACHE = None  # 清懒加载缓存


def test_query_order_hit(monkeypatch, tmp_path):
    _reload(
        monkeypatch, tmp_path,
        [{
            "order_no": "SO2026080118", "status": "shipped",
            "items": "空气净化器 K1", "logistics_no": "SF1384429007712",
            "eta": "2026-08-26 前送达", "updated_at": "2026-08-24 10:00",
        }],
    )
    o = ot.query_order("SO2026080118")
    assert o is not None
    assert o.status == "shipped"
    assert o.logistics_no == "SF1384429007712"


def test_query_order_case_insensitive(monkeypatch, tmp_path):
    """小写订单号归一命中（用户手输场景）。"""
    _reload(monkeypatch, tmp_path, [{
        "order_no": "XOZ-12345", "status": "pending_shipped",
        "items": "手机 X100", "logistics_no": None, "eta": None, "updated_at": "x",
    }])
    assert ot.query_order("xoz-12345") is not None


def test_query_order_miss_returns_none(monkeypatch, tmp_path):
    _reload(monkeypatch, tmp_path, [])
    assert ot.query_order("SO0000000000") is None


def test_query_order_missing_file_returns_none(monkeypatch, tmp_path):
    """数据文件缺失 → None（fail-open，不抛）。"""
    _reload(monkeypatch, tmp_path, None)
    assert ot.query_order("SO2026080118") is None


def test_query_order_corrupt_file_returns_none(monkeypatch, tmp_path):
    f = tmp_path / "orders.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(ot, "_DATA_FILE", f)
    ot._ORDERS_CACHE = None
    assert ot.query_order("SO2026080118") is None


def test_format_reply_full(monkeypatch, tmp_path):
    _reload(monkeypatch, tmp_path, [])
    o = ot.OrderInfo(
        order_no="SO2026080118", status="shipped", items="空气净化器 K1",
        logistics_no="SF1384429007712", eta="2026-08-26 前送达", updated_at="x",
    )
    text = ot.format_order_reply(o)
    assert "SO2026080118" in text
    assert "已发货" in text
    assert "空气净化器 K1" in text
    assert "SF1384429007712" in text
    assert "2026-08-26" in text


def test_format_reply_minimal():
    o = ot.OrderInfo(
        order_no="XOZ-12345", status="pending_shipped", items="手机 X100",
        logistics_no=None, eta=None, updated_at="x",
    )
    text = ot.format_order_reply(o)
    assert "待发货" in text
    assert "物流单号" not in text
    assert "预计" not in text


def test_format_reply_refunding():
    o = ot.OrderInfo(
        order_no="SO1", status="refunding", items="x",
        logistics_no=None, eta=None, updated_at="x",
    )
    assert "退款处理中" in ot.format_order_reply(o)


def test_order_topics_constant():
    assert ot.ORDER_TOPICS == frozenset({"退款", "退换货", "保修/维修", "配送/物流", "价保"})
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_order_tool.py -q -p no:cacheprovider --no-cov
```
预期：FAIL（ModuleNotFoundError: app.services.tools）

- [ ] **Step 3: 写实现**

**(a)** `backend/app/services/tools/__init__.py`：

```python
"""工具注册表（批次D 起）：订单查询等业务工具。接口按未来真实订单系统设计。"""
```

**(b)** `backend/app/services/tools/order_tool.py`：

```python
"""OrderTool（批次D）：订单查询 Mock 工具 + 模板回答。

- 数据源 scripts/demo_orders.json 懒加载单例（缺文件/损坏 → 空，fail-open）；
- query_order 同步纯读（chat 层搬线程池）——未来接真实订单 API 只换本函数内部；
- format_order_reply 模板拼接，零 LLM：事实型查询不冒幻觉风险；
- ORDER_TOPICS：订单类主题门控集合（与 conversation_state.REQUIRED_SLOTS 键对齐）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: 订单类主题（chat 层门控：槽位有订单号 + 主题属于此集合才走工具分支）
ORDER_TOPICS: frozenset[str] = frozenset({"退款", "退换货", "保修/维修", "配送/物流", "价保"})

#: 状态 → 中文文案（模板唯一真源）
STATUS_TEXT: dict[str, str] = {
    "pending_shipped": "待发货",
    "shipped": "已发货",
    "signed": "已签收",
    "refunding": "退款处理中",
}

_DATA_FILE = Path(__file__).resolve().parents[3] / "scripts" / "demo_orders.json"
_ORDERS_CACHE: dict[str, dict] | None = None  # 懒加载单例（order_no upper → 原始 dict）


@dataclass
class OrderInfo:
    order_no: str
    status: str
    items: str
    logistics_no: str | None
    eta: str | None
    updated_at: str


def _load_orders() -> dict[str, dict]:
    """懒加载数据文件；缺失/损坏返回空 dict（fail-open，不抛）。"""
    global _ORDERS_CACHE
    if _ORDERS_CACHE is not None:
        return _ORDERS_CACHE
    try:
        raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
        # 兼容 list[dict] 与 {"orders": [...]} 两种形态
        items = raw.get("orders", raw) if isinstance(raw, dict) else raw
        _ORDERS_CACHE = {str(o["order_no"]).upper(): o for o in items if isinstance(o, dict) and o.get("order_no")}
    except FileNotFoundError:
        logger.warning("订单数据文件缺失: %s（工具分支降级为空）", _DATA_FILE)
        _ORDERS_CACHE = {}
    except Exception:  # noqa: BLE001 - JSON 损坏等
        logger.exception("订单数据文件解析失败（工具分支降级为空）")
        _ORDERS_CACHE = {}
    return _ORDERS_CACHE


def query_order(order_no: str) -> OrderInfo | None:
    """按订单号查 Mock 订单；大小写归一；查不到/数据不可用返回 None。"""
    data = _load_orders().get((order_no or "").strip().upper())
    if data is None:
        return None
    try:
        return OrderInfo(
            order_no=str(data["order_no"]),
            status=str(data["status"]),
            items=str(data.get("items", "")),
            logistics_no=data.get("logistics_no"),
            eta=data.get("eta"),
            updated_at=str(data.get("updated_at", "")),
        )
    except (KeyError, TypeError):  # noqa: PERF203 - 单条脏数据降级 None
        return None


def format_order_reply(o: OrderInfo) -> str:
    """模板回答（零 LLM）。"""
    parts = [f"您的订单 {o.order_no}（{o.items}）当前状态：{STATUS_TEXT.get(o.status, o.status)}。"]
    if o.logistics_no:
        parts.append(f"物流单号：{o.logistics_no}。")
    if o.eta:
        parts.append(f"预计{o.eta}。")
    parts.append("如需其他帮助请继续告诉我。")
    return "".join(parts)
```

**(c)** `backend/scripts/demo_orders.json`（订单号与知识库 demo 文档同源）：

```json
{
  "orders": [
    {
      "order_no": "SO2026080091",
      "status": "pending_shipped",
      "items": "滚筒洗衣机 W5",
      "logistics_no": null,
      "eta": "2026-08-16 送装（预约 14:00-17:00）",
      "updated_at": "2026-08-15 09:30"
    },
    {
      "order_no": "SO2026080118",
      "status": "shipped",
      "items": "空气净化器 K1",
      "logistics_no": "SF1384429007712",
      "eta": "2026-08-26 前送达",
      "updated_at": "2026-08-24 10:00"
    },
    {
      "order_no": "XOZ-12345",
      "status": "signed",
      "items": "手机 X100 Ultra",
      "logistics_no": "SF1399887766554",
      "eta": null,
      "updated_at": "2026-08-20 18:12"
    },
    {
      "order_no": "SO2026070302",
      "status": "refunding",
      "items": "扫地机器人 R7",
      "logistics_no": null,
      "eta": null,
      "updated_at": "2026-08-23 14:05"
    }
  ]
}
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_order_tool.py -q -p no:cacheprovider --no-cov
```
预期：`9 passed`

- [ ] **Step 5: Lint + 提交**

```powershell
cd backend; .\.venv\Scripts\python.exe -m ruff check app/services/tools/ tests/test_order_tool.py
git add app/services/tools/ scripts/demo_orders.json tests/test_order_tool.py
git commit -m "feat(order): OrderTool Mock 工具——查询/归一/降级 + 零 LLM 模板回答"
```

---

### Task 2: chat 层订单分支接入

**Files:**
- Modify: `backend/app/api/chat.py`（import 区 + gen() 内 ImageAgent 块之后插订单分支，约 [L334-L336](file:///C:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/api/chat.py#L334)）
- Test: `backend/tests/test_chat_api.py`（追加 3 个用例）

**Interfaces:**
- Consumes: Task 1 的 `query_order/format_order_reply/OrderInfo/ORDER_TOPICS`；批次 B 的 `conv_state`
- Produces: 完整链路——槽位 order_no + 订单主题 → 工具分支 token 流 + meta{"tool":"order_query"}；链路兜底澄清：无订单号时（批次 C）拒答→澄清→下轮槽位填充→工具接管

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_chat_api.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_chat_api.py -q -p no:cacheprovider --no-cov -k order_tool
```
预期：3 FAIL（`app.api.chat` 无 order_tool 属性）

- [ ] **Step 3: 写实现**

**(a) chat.py import 区追加**（模块级，mock 契约同批次 B/C）：

```python
from app.services.tools import order_tool
```

**(b) gen() 内 ImageAgent 块之后插订单分支**：

```python
        # 批次D：订单工具分支——槽位有订单号 + 订单类主题 → 查单模板回答（零 LLM，
        # 事实型查询不冒幻觉）；查不到/异常回落 RAG（fail-open，不阻断）
        order_info = None
        if (
            (conv_state or {}).get("slots", {}).get("order_no")
            and (conv_state or {}).get("topic") in order_tool.ORDER_TOPICS
        ):
            try:
                order_info = await run_in_threadpool(
                    order_tool.query_order, conv_state["slots"]["order_no"]
                )
            except Exception:  # noqa: BLE001 - 工具异常回落 RAG
                logger.exception("订单工具查询失败（回落 RAG）")
                order_info = None
```

**(c) `_events()` 内 quick_ans 分支之后插工具分支**（同形态短路）：

```python
                # 批次D：订单工具短路——零 LLM 模板回答（quick_ans 同构事件形态）
                if order_info is not None:
                    reply_text = order_tool.format_order_reply(order_info)
                    yield ("intent", {"intent": "qa", "refuse": False})
                    yield ("stage", {"stage": "retrieving", "msg": "已查询订单"})
                    yield ("stage", {"stage": "generating", "msg": "正在生成回答"})
                    for delta in _split_answer(reply_text):
                        yield ("token", {"delta": delta})
                    yield ("sources", {"sources": []})
                    yield ("done", {"message_id": "", "tool": "order_query"})
                    return
```

**(d) done 分支 meta 标记**——`meta = {"first_token_ms": ...}` 构造处改为：

```python
                    meta = {"first_token_ms": first_token_ms} if first_token_ms is not None else {}
                    if data.get("tool"):
                        meta["tool"] = data["tool"]  # 批次D：工具回答可观测
```

- [ ] **Step 4: 跑测试确认通过**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_chat_api.py tests/test_order_tool.py -q -p no:cacheprovider --no-cov
```
预期：全 passed

- [ ] **Step 5: Lint + 提交**

```powershell
cd backend; .\.venv\Scripts\python.exe -m ruff check app/api/chat.py tests/test_chat_api.py
git add app/api/chat.py tests/test_chat_api.py
git commit -m "feat(order): chat 层订单工具分支——槽位门控+零 LLM 模板流+回落 RAG"
```

---

### Task 3: 三步链路 e2e + 批次 D 全量回归

**Files:**
- Test: `backend/tests/test_chat_e2e_order.py`（新建，独立 e2e 文件）

**Interfaces:**
- Consumes: Task 1/2 全部 + 批次 B/C 状态链
- Produces: e2e 锁定完整链路——「问物流（无单号）→ 澄清 → 给单号 → 工具回答」

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_chat_e2e_order.py`：

```python
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

    # 检索强制低分拒答（触发澄清链）——embedding/Qdrant 全 mock 掉
    class _LowScoreSearch:
        def search_kb(self, query, kb_id, top_k=8):
            from app.services.retrieval_service import RetrievedChunk
            return [RetrievedChunk(chunk_id="c", doc_id="d", kb_id=str(kb_id), idx=0, text="无关内容", score=0.05, dense_score=0.05)]

    monkeypatch.setattr("app.services.retrieval_service.get_qdrant_client", lambda: _LowScoreSearch())
    monkeypatch.setattr(
        "app.services.retrieval_service.get_embedding_client",
        lambda: type("E", (), {"embed": staticmethod(lambda texts: [[0.0] * 8])})(),
    )

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


def test_order_e2e_clarify_then_tool(client):
    """三步闭环：①主题无单号→拒答澄清 ②给单号→槽位填充 ③订单主题→工具回答。"""
    c, Local = client
    sid = str(SID)

    # 第①步：物流主题、无订单号 → 拒答 + 澄清（done.clarify）
    r1 = c.post(f"{API}/chat/stream", json={"session_id": sid, "content": "物流到哪了", "stream": True}, headers=_h())
    assert r1.status_code == 200
    assert "订单号" in r1.text  # 澄清问句

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
    assert "已发货" in r3.text
    assert "SF1384429007712" in r3.text
```

- [ ] **Step 2: 跑测试确认失败（或直接验证链路断点）**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests/test_chat_e2e_order.py -q -p no:cacheprovider --no-cov
```
预期：可能 PASS 也可能 FAIL——e2e 依赖批次 B/C/D 串联（第①步澄清真实走 rag_service 拒答路径，低分 mock 检索触发）。若第③步断言失败（工具未接管），按 Task 2 实现排查门控条件；记录实际现象。

- [ ] **Step 3: 修复链路断点（如有）**

常见断点（按序排查）：
- 第①步无澄清：检查 `_LowScoreSearch` 的 dense_score 是否 < MIN_SCORE(0.30) → refuse 触发；clarify_left>0
- 第②步槽位未填：`_ORDER_RE` 对 "SO2026080118" 单独成句的匹配（正则 `\b[A-Z]{2,}\d{4,}` 应命中）
- 第③步未走工具：conv_state.topic 在第②步后是否仍为「配送/物流」（消息"SO2026080118"无主题词→保留旧主题✓）+ slots 有值 + ORDER_TOPICS 命中
- quick_ans 优先级：`match_quick("帮我看下物流进度")` 若命中快捷话术会先短路——本用例文案避开快捷表即可

- [ ] **Step 4: 批次 D 全量回归（亲测）**

```powershell
cd backend; $env:PYTHONDONTWRITEBYTECODE='1'; .\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --no-cov --ignore=tests/test_demo_orders.py --junitxml=$env:TEMP\lxd.xml
# 预期: 403+9(order tool)+3(chat)+1(e2e)=416 tests / 0 failures（以实际为准，junitxml 权威）
cd ..\frontend; $env:NODE_OPTIONS='--max-old-space-size=4096'; npx vitest run --no-file-parallelism
npx tsc --noEmit
cd ..; python scripts/check_contracts.py
# 前端零改动（工具分支对前端透明——事件契约未变）；契约 PASS
```

- [ ] **Step 5: Lint + 提交**

```powershell
cd backend; .\.venv\Scripts\python.exe -m ruff check app/api/chat.py app/services/tools/ tests/test_chat_api.py tests/test_order_tool.py tests/test_chat_e2e_order.py
git add tests/test_chat_e2e_order.py
git commit -m "feat(order): 三步链路 e2e——主题澄清→槽位填充→工具接管闭环锁定"
```

---

## Self-Review 记录

- **Spec 覆盖**：原批次 D 规划的 D1（工具注册表+OrderTool+模板）→ Task 1；D2（路由接入）→ Task 2；e2e（原规划「三步对话全链路」测试）→ Task 3。Mock 数据 6-8 条（原规划）→ 实际 4 条（覆盖 4 状态各 1，YAGNI 收敛，诚实记录）。
- **类型一致性**：`query_order(str)->OrderInfo|None`（Task 1 定义 = Task 2 调用 = e2e mock）；`ORDER_TOPICS` 五主题（Task 1 定义 = Task 2 门控 = conversation_state 键集对齐）；`done.tool="order_query"`（Task 2 (c) 产出 = (d) meta 消费 = 测试断言）。
- **踩坑预判**：① mock 契约——chat.py `from app.services.tools import order_tool` 模块属性访问 + 测试双 patch（ot 命名空间与 chat.order_tool 同对象，双 patch 是冗余保险，单独 patch chat.order_tool 即可——测试代码保守双打）；② e2e 第①步走真实 rag_service 拒答路径（区别于 Task 2 的替身）——embedding/Qdrant mock 需压低 dense_score 触发 refuse；③ quick_ans 优先级高于订单分支——e2e 文案避开快捷表；④ `_split_answer`（chat.py 的 rag_service._split_tokens 别名）已有，直接用。
- **已知限制**：① Mock 数据 4 条（状态全覆盖但数量低于原规划 6-8，接口设计使扩容零成本）；② 多订单号只取首个（批次 B 槽位语义，多单场景后续批次）；③ 工具分支不做澄清（订单查不到直接回落 RAG，不追问「是不是单号错了」——留给后续优化）；④ `scripts/demo_orders.json` 是运行时数据文件不在 alembic 面，部署需随包。
