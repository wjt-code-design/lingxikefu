"""Telemetry API 测试（ErrorBoundary TODO 落地）：前端错误上报端点（纯日志，无 DB）。"""
from __future__ import annotations

import logging

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_report_frontend_error_ok(client, caplog):
    """POST /telemetry/frontend-error → 204，且写结构化日志。"""
    with caplog.at_level(logging.ERROR, logger="lingxi"):
        r = client.post(
            "/api/v1/telemetry/frontend-error",
            json={
                "message": "boom",
                "stack": "at Foo (foo.tsx:1)",
                "component": "ChatContainer",
                "url": "http://localhost/chat",
                "user_agent": "vitest",
            },
        )
    assert r.status_code == 204
    assert any("frontend_error" in rec.message and "boom" in rec.message for rec in caplog.records)


def test_report_frontend_error_empty_body(client):
    """空 body 字段默认空串，仍 204（前端可能只传部分字段）。"""
    r = client.post("/api/v1/telemetry/frontend-error", json={})
    assert r.status_code == 204


def test_report_frontend_error_log_injection_escaped(client, caplog):
    """message 含换行/控制字符 → JSON 编码后记录，不污染日志行（防日志伪造）。"""
    import json as _json

    evil = '正常日志\n2026-01-01 伪造审计条目 [INFO] hacked'
    with caplog.at_level(logging.ERROR, logger="lingxi"):
        r = client.post(
            "/api/v1/telemetry/frontend-error",
            json={"message": evil, "stack": ""},
        )
    assert r.status_code == 204
    rec = next(rec for rec in caplog.records if "frontend_error" in rec.message)
    # message 以 JSON 字符串形式记录（\n 转义为 \\n），原始换行不直接出现在日志里
    assert _json.loads(rec.message.split("message=")[1].split(" stack=")[0]) == evil
    assert "\n" not in rec.message.split("message=")[1].split(" stack=")[0]


def test_report_frontend_error_rate_limited(client, caplog):
    """同 IP 超过窗口上限（60s/10 条）→ 静默 204 且不再写日志（防刷屏）。"""
    from app.api.telemetry import _RATE_LIMIT_MAX, _recent

    _recent.clear()  # 重置滑动窗口，避免前序测试消耗配额
    with caplog.at_level(logging.ERROR, logger="lingxi"):
        for _ in range(_RATE_LIMIT_MAX + 3):
            r = client.post("/api/v1/telemetry/frontend-error", json={"message": "x"})
            assert r.status_code == 204
    n = sum(1 for rec in caplog.records if "frontend_error" in rec.message)
    assert n == _RATE_LIMIT_MAX  # 超限部分被丢弃，不写日志
