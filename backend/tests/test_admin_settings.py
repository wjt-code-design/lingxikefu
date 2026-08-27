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
    """admin：200 + 分组字段（env/model/rag/rate_limit/quota）与 settings 真源一致。"""
    r = client.get(f"{API}/admin/settings", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    # 顶层分组齐全
    for group in ("env", "model", "rag", "rate_limit", "quota"):
        assert group in data
    assert data["env"] == settings.ENV
    # model 分组（2026-08-27 收敛：仅 LongCat，无备用模型）
    assert data["model"]["provider"] == settings.CHAT_PROVIDER
    assert data["model"]["model"] == settings.LONGCAT_CHAT_MODEL
    assert data["model"]["fallback"] is None
    assert data["model"]["embedding_provider"] == settings.EMBEDDING_PROVIDER
    assert data["model"]["embedding_model"] == settings.EMBEDDING_MODEL
    # rag 分组
    assert data["rag"]["top_k"] == settings.RETRIEVAL_TOP_K
    assert data["rag"]["min_score"] == settings.MIN_SCORE
    assert data["rag"]["hybrid"] == settings.RAG_ENABLE_HYBRID
    assert data["rag"]["chunk_size"] == settings.CHUNK_SIZE
    assert data["rag"]["chunk_overlap"] == settings.CHUNK_OVERLAP
    assert data["rag"]["answer_cache_enabled"] == settings.ANSWER_CACHE_ENABLED
    assert data["rag"]["answer_cache_threshold"] == settings.ANSWER_CACHE_THRESHOLD
    assert data["rag"]["max_upload_mb"] == settings.MAX_UPLOAD_MB
    # rate_limit / quota 分组
    assert data["rate_limit"]["enabled"] == settings.RATE_LIMIT_ENABLED
    assert data["quota"]["daily_limit"] == settings.DAILY_QUOTA_LIMIT


def test_admin_settings_forbidden_for_user(client):
    """非 admin → 403。"""
    r = client.get(f"{API}/admin/settings", headers=_h(USER, "user"))
    assert r.status_code == 403
