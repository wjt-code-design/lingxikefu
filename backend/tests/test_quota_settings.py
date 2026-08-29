"""配额 DB 化测试（架构一期 6）：app_settings KV 写通道 + daily_limit 动态上限。

手法：TestClient + admin JWT（照 test_admin_settings.py）；PUT 写通道需要 DB，
内存 SQLite + get_db dependency override（照 test_quota.py）。QuotaService 模块级
单例注入同一 SQLite session 工厂，保证 daily_limit() 的 KV 读与 PUT 写落在同一库。
"""
from __future__ import annotations

import uuid

import pytest
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.app_setting import AppSetting
from app.models.base import Base
from app.services.quota import DAILY_LIMIT_KV_KEY, QuotaService, get_quota_service
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _h(uid: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(uid), role=role)}"}


@pytest.fixture
def kv_sessionmaker():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[AppSetting.__table__])
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def client(kv_sessionmaker):
    def _override():
        db = kv_sessionmaker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    svc = get_quota_service()
    svc.session_factory = kv_sessionmaker  # KV 读与 PUT 写同一 SQLite
    svc.invalidate_limit_cache()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    svc.session_factory = None
    svc.invalidate_limit_cache()


def test_put_quota_updates_daily_limit(client):
    """admin PUT 写 KV → daily_limit() 读到覆盖值 + GET settings 的 quota 组读生效值。"""
    r = client.put(f"{API}/admin/settings/quota", json={"daily_quota_limit": 500}, headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    assert r.json()["daily_limit"] == 500
    # KV 覆盖生效（PUT 已清缓存，立即读到新值）
    assert get_quota_service().daily_limit() == 500
    # GET /admin/settings 的 quota 组读生效值（原 :43 直读 settings 的漂移点已修）
    r2 = client.get(f"{API}/admin/settings", headers=_h(ADMIN, "admin"))
    assert r2.status_code == 200
    assert r2.json()["quota"]["daily_limit"] == 500


def test_daily_limit_falls_back_to_settings(client):
    """未写 KV（无覆盖行）→ 回退 settings 常量。"""
    assert get_quota_service().daily_limit() == settings.DAILY_QUOTA_LIMIT


def test_daily_limit_falls_back_when_kv_db_down():
    """KV 读失败（DB 挂）→ 回退 settings 而非拒绝服务（fail-open 方向）。"""

    def _boom():
        raise ConnectionError("app_settings db down")

    svc = QuotaService(session_factory=_boom)
    assert svc.daily_limit() == settings.DAILY_QUOTA_LIMIT


def test_daily_limit_cache_within_ttl_and_invalidate(client, kv_sessionmaker):
    """60s 进程内 TTL：TTL 内直改 DB 不穿透；清缓存（PUT 后主动失效）立即读新值。"""
    r = client.put(f"{API}/admin/settings/quota", json={"daily_quota_limit": 300}, headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    assert get_quota_service().daily_limit() == 300
    # 绕过写通道直改 KV（模拟外部改库）→ TTL 内仍读缓存
    with kv_sessionmaker() as db:
        row = db.get(AppSetting, DAILY_LIMIT_KV_KEY)
        row.value = 999
        db.commit()
    assert get_quota_service().daily_limit() == 300
    get_quota_service().invalidate_limit_cache()
    assert get_quota_service().daily_limit() == 999


def test_put_quota_forbidden_for_user(client):
    """非 admin PUT → 403（读通道同理）。"""
    body = {"daily_quota_limit": 500}
    assert client.put(f"{API}/admin/settings/quota", json=body, headers=_h(USER, "user")).status_code == 403
    assert client.get(f"{API}/admin/settings", headers=_h(USER, "user")).status_code == 403


@pytest.mark.parametrize("bad", [0, -5, "abc", 1.5, None])
def test_put_quota_invalid_value_422(client, bad):
    """非法值（≤0 / 非整数）→ 422，且不写入 KV。"""
    r = client.put(f"{API}/admin/settings/quota", json={"daily_quota_limit": bad}, headers=_h(ADMIN, "admin"))
    assert r.status_code == 422
    assert get_quota_service().daily_limit() == settings.DAILY_QUOTA_LIMIT
