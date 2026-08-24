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
