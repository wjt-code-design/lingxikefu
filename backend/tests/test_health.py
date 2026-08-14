"""/health 健康检查单测（BU-01 DoD：GET /health 返回 {"status":"ok",...}）。"""
from __future__ import annotations

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health_returns_ok() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["tenant"] == "default"


def test_health_has_request_id_header() -> None:
    """请求 ID 中间件生效：响应带 X-Request-ID（与统一错误模型 request_id 对齐）。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")
