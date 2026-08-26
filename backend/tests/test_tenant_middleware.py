"""租户中间件安全测试（P1-②：租户身份可伪造 + 读写错位）。

修复前漏洞：tenant_middleware 采信 X-Tenant-ID 头 / Host 子域名（白名单只挡
www/api/admin/app 四前缀，不校验租户合法性）；而全仓鉴权读写硬编码
settings.TENANT_DEFAULT，唯 kb_lookup 走 get_current_tenant() 动态读 —— 同一请求内
鉴权按 default、KB 查询按可伪造头，读写彻底错位；_kb_cache 以可伪造串为 key 无界增长。

修复语义（短期单租户正解）：中间件恒设 settings.TENANT_DEFAULT，X-Tenant-ID 头 /
Host 子域名一律不采信；kb_lookup 与全局一致读 TENANT_DEFAULT。
多租户成为真需求时按 ADR 重做（届时加租户注册表白名单校验）。
"""
from __future__ import annotations

import uuid

import app.models.knowledge  # noqa: F401 - 注册 KnowledgeBase 模型到 Base.metadata
import pytest
from app.core.config import settings
from app.core.database import get_db
from app.core.tenant import current_tenant
from app.main import app
from app.models.base import Base
from app.models.knowledge import KnowledgeBase
from app.services import kb_lookup
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

DEFAULT_KB_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


@pytest.fixture
def local():
    """内存 SQLite + KB 表：预置一条 default 租户 KB 供 kb_lookup 断言。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[KnowledgeBase.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    with Local() as db:
        db.add(KnowledgeBase(id=DEFAULT_KB_ID, tenant_id=settings.TENANT_DEFAULT, name="默认库"))
        db.commit()

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    yield Local
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_kb_cache():
    """用例前后重置 kb_lookup 缓存，隔离用例（兼容修复前 dict / 修复后单条目两种形状）。"""

    def _do() -> None:
        cache = getattr(kb_lookup, "_kb_cache", None)
        if isinstance(cache, dict):
            cache.clear()
        else:
            kb_lookup._kb_cache = None

    _do()
    yield
    _do()


def test_forged_tenant_header_is_ignored(local) -> None:
    """带 X-Tenant-ID: evil 的请求与不带行为一致：响应头恒为 TENANT_DEFAULT。"""
    with TestClient(app) as c:
        resp = c.get("/health", headers={"X-Tenant-ID": "evil"})
    assert resp.status_code == 200
    assert resp.headers["X-Tenant-ID"] == settings.TENANT_DEFAULT


def test_subdomain_host_is_ignored(local) -> None:
    """Host 子域名推断不再采信：{evil}.lingxi.example.com 也恒为 TENANT_DEFAULT。"""
    with TestClient(app) as c:
        resp = c.get("/health", headers={"Host": "evil.lingxi.example.com"})
    assert resp.status_code == 200
    assert resp.headers["X-Tenant-ID"] == settings.TENANT_DEFAULT


def test_in_request_get_current_tenant_ignores_header(local) -> None:
    """请求内 get_current_tenant() 与鉴权侧一致：伪造头无法影响 ContextVar 值。"""
    from app.core.tenant import get_current_tenant

    seen: dict[str, str] = {}
    probe_path = "/__test_probe__/tenant"

    @app.get(probe_path)
    async def _probe() -> dict:
        seen["tenant"] = get_current_tenant()
        return {"tenant": seen["tenant"]}

    try:
        with TestClient(app) as c:
            resp = c.get(probe_path, headers={"X-Tenant-ID": "evil"})
            plain = c.get(probe_path)
        assert resp.status_code == 200
        assert plain.status_code == 200
        assert seen["tenant"] == settings.TENANT_DEFAULT, "带头请求"
        assert seen["tenant"] == settings.TENANT_DEFAULT, "不带请求"
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != probe_path]


def test_get_latest_kb_id_ignores_forged_contextvar_tenant(local) -> None:
    """kb_lookup 与全局鉴权一致：ContextVar 被置为伪造值时仍按 TENANT_DEFAULT 读 KB。"""
    token = current_tenant.set("evil")
    try:
        with local() as db:
            kb_id = kb_lookup.get_latest_kb_id(db)
        assert kb_id == DEFAULT_KB_ID
    finally:
        current_tenant.reset(token)


class _CountingSession:
    """代理 OrmSession.scalar 计数，验证 TTL 内缓存命中不再查库。"""

    def __init__(self, real) -> None:
        self._real = real
        self.scalar_calls = 0

    def scalar(self, stmt, *args, **kwargs):
        self.scalar_calls += 1
        return self._real.scalar(stmt, *args, **kwargs)


def test_kb_cache_hit_within_ttl_queries_once(local) -> None:
    """回归守卫：60s TTL 内第二次调用走缓存，不重复查库（重构单条目后语义不变）。"""
    with local() as db:
        proxy = _CountingSession(db)
        first = kb_lookup.get_latest_kb_id(proxy)
        second = kb_lookup.get_latest_kb_id(proxy)
    assert first == DEFAULT_KB_ID
    assert second == DEFAULT_KB_ID
    assert proxy.scalar_calls == 1


def test_no_kb_result_not_cached(local) -> None:
    """回归守卫：无 KB 不缓存 → 新建 KB 后立即可感知（原 B4 语义保留）。"""
    with local() as db:
        db.execute(delete(KnowledgeBase))
        db.commit()
        proxy = _CountingSession(db)
        assert kb_lookup.get_latest_kb_id(proxy) is None

    with local.begin() as _tx:  # noqa: F841 - 重新拿会话写一条新 KB
        pass
    with local() as db:
        db.add(KnowledgeBase(id=uuid.UUID("44444444-4444-4444-4444-444444444444"), name="新库"))
        db.commit()
        assert kb_lookup.get_latest_kb_id(db) == uuid.UUID("44444444-4444-4444-4444-444444444444")
