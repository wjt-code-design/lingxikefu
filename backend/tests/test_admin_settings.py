"""Admin Settings API 测试（Phase 4）：/admin/settings 只读配置视图 + 权限。"""
from __future__ import annotations

import uuid

import pytest
from app.core.config import settings
from app.core.security import create_access_token
from app.main import app
from fastapi.testclient import TestClient

API = "/api/v1"

ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _h(uid: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(uid), role=role)}"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_admin_settings_structure(client):
    """admin：200 + 分组字段（env/model/rag/rate_limit）与 settings 真源一致。

    2026-09-27（审计 M5 任务 2）：评测口径一族（top_k/min_score/hybrid/chunk*/模型）
    此前全部写成 `== settings.X`——期望值取自被测实现，常量改坏（RETRIEVAL_TOP_K
    5→1、MIN_SCORE 0.30→0.99、换模型）时两侧一起错、评测门禁结果变了测试仍全绿。
    现按 BASELINE.sha256 头部冻结口径钉**字面量**（RETRIEVAL_TOP_K=5 / MIN_SCORE=0.30 /
    LongCat-2.0 / local BAAI/bge-base-zh-v1.5）；回显机制本身仍由其余 `== settings.X`
    断言覆盖（那类"端点透传配置"是合法期望值）。
    """
    r = client.get(f"{API}/admin/settings", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    # 顶层分组齐全
    for group in ("env", "model", "rag", "rate_limit"):
        assert group in data
    assert "quota" not in data  # 额度系统已移除（2026-09-06）
    assert data["env"] == settings.ENV
    # model 分组（评测口径字面量钉：生成/嵌入模型即基线可比性的一部分，
    # BASELINE.sha256 头部「LongCat-2.0（backend/.env，CHAT_PROVIDER=longcat）」）
    assert data["model"]["provider"] == "longcat"
    assert data["model"]["model"] == "LongCat-2.0"
    assert data["model"]["fallback"] is None
    assert data["model"]["embedding_provider"] == "local"
    assert data["model"]["embedding_model"] == "BAAI/bge-base-zh-v1.5"
    # rag 分组（评测口径字面量钉：检索条数/拒答阈值/混合检索/切片参数直接进入
    # faithfulness·recall@5·诚实性拒答指标的判定面；数字=冻结清单与 config 现值）
    assert data["rag"]["top_k"] == 5  # 降噪扫描定值（8→5，precision@5 20%→32%）
    assert data["rag"]["min_score"] == 0.30  # L4 拒答阈值（原硬编码 0.30 提为配置）
    assert data["rag"]["hybrid"] is True  # ADR-2026-08-16：hybrid 为现势检索口径
    assert data["rag"]["chunk_size"] == 500
    assert data["rag"]["chunk_overlap"] == 50
    # 以下为「回显=配置」的透传语义（值本身不属评测门禁口径），保留同源比较
    assert data["rag"]["answer_cache_enabled"] == settings.ANSWER_CACHE_ENABLED
    assert data["rag"]["answer_cache_threshold"] == settings.ANSWER_CACHE_THRESHOLD
    assert data["rag"]["max_upload_mb"] == settings.MAX_UPLOAD_MB
    # rate_limit 分组
    assert data["rate_limit"]["enabled"] == settings.RATE_LIMIT_ENABLED


def test_admin_settings_forbidden_for_user(client):
    """非 admin → 403。"""
    r = client.get(f"{API}/admin/settings", headers=_h(USER, "user"))
    assert r.status_code == 403
