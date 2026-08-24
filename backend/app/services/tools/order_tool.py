"""OrderTool（批次D）：订单查询 Mock 工具 + 模板回答。

- 数据源 scripts/demo_orders.json 懒加载单例（缺文件/损坏 → 空，fail-open）；
- query_order 同步纯读（chat 层搬线程池）——未来接真实订单 API 只换本函数内部；
- format_order_reply 模板拼接，零 LLM：事实型查询不冒幻觉风险；
- ORDER_TOPICS：派生自 conversation_state.REQUIRED_SLOTS（大扫查修复：消除手工双源同步）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.services.conversation_state import REQUIRED_SLOTS, SLOT_ORDER_NO

logger = logging.getLogger(__name__)

#: 订单类主题（chat 层门控：槽位有订单号 + 主题属于此集合才走工具分支）。
#: 大扫查修复：从 REQUIRED_SLOTS 派生（要求含订单号槽位的主题），不再手工维护第二份。
ORDER_TOPICS: frozenset[str] = frozenset(
    t for t, slots in REQUIRED_SLOTS.items() if SLOT_ORDER_NO in slots
)

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
