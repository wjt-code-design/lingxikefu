"""P0-2 工具注册表：order_tool 显式注册，调用方经注册表获取元数据与执行函数。"""
from __future__ import annotations

import json

from app.services.tools import TOOL_REGISTRY, get_tool, order_tool


def _seed_orders(tmp_path, monkeypatch):
    """注入确定性订单数据（绕过真实 demo 数据文件）。"""
    data_file = tmp_path / "orders.json"
    data_file.write_text(
        json.dumps({
            "orders": [{
                "order_no": "TEST001", "status": "shipped", "items": "洗衣机",
                "logistics_no": "SF001", "eta": "明天", "updated_at": "x",
            }]
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(order_tool, "_DATA_FILE", data_file)
    monkeypatch.setattr(order_tool, "_ORDERS_CACHE", None)


class TestRegistryContract:
    def test_registry_is_nonempty_dict(self):
        assert isinstance(TOOL_REGISTRY, dict)
        assert "order_query" in TOOL_REGISTRY

    def test_order_tool_descriptor_fields(self):
        desc = TOOL_REGISTRY["order_query"]
        assert desc.name == "order_query"
        assert desc.description  # 职责说明非空
        assert "order_no" in desc.parameters  # 参数 schema 含订单号
        assert desc.executor is order_tool.query_order  # 执行函数指向真实实现
        assert desc.formatter is order_tool.format_order_reply
        assert desc.topics == order_tool.ORDER_TOPICS  # 门控主题派生一致

    def test_get_tool_lookup(self):
        assert get_tool("order_query") is TOOL_REGISTRY["order_query"]
        assert get_tool("not_exist") is None


class TestOrderThroughRegistry:
    def test_executor_reads_seeded_orders(self, tmp_path, monkeypatch):
        _seed_orders(tmp_path, monkeypatch)
        info = get_tool("order_query").executor("test001")  # 大小写归一
        assert info is not None
        assert info.order_no == "TEST001"
        assert info.status == "shipped"

    def test_executor_miss_returns_none(self, tmp_path, monkeypatch):
        _seed_orders(tmp_path, monkeypatch)
        assert get_tool("order_query").executor("NO_SUCH_ORDER") is None

    def test_formatter_uses_status_text(self, tmp_path, monkeypatch):
        _seed_orders(tmp_path, monkeypatch)
        desc = get_tool("order_query")
        info = desc.executor("TEST001")
        reply = desc.formatter(info)
        assert "已发货" in reply
        assert "SF001" in reply
