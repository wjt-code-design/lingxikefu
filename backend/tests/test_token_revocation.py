"""Token 吊销测试（M1 + S1）：Redis 故障时 fail-closed，防已吊销 token 复活 / refresh 并发重放。

A2（审计 M4 2026-09-27）：原 `_SeqRedis` 的 mock 契约与真 Redis 不一致——`exists(key)` 忽略
入参恒真、`set()` 按调用序而非按 key 返回，于是「黑名单查错键」（`_PREFIX` 漂移）这类致命错
整套测试全绿（变异已证实）。本文件一律改用 fakeredis（真 key 语义，requirements.txt 已装），
断言必须**按 jti 分键**成立，且登出→旧 token 失效的往返经真鉴权链路走一遍。
"""
from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest
from app.core import token_revocation
from app.core.database import get_db
from app.main import app
from app.models.base import Base
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

_FUTURE_EXP = 9999999999  # 远未来时间戳，保证 TTL 为正


def _redis_down():
    raise RuntimeError("redis connection refused")


@pytest.fixture
def rdb():
    """按 key 作用域的 Redis 替身（fakeredis 真语义：SET/EXISTS/NX 均以键为轴）。

    每用例独立实例，杜绝跨用例残留键导致的假命中。
    """
    server = fakeredis.FakeStrictRedis(decode_responses=True)
    with patch("app.core.token_revocation.get_redis", return_value=server):
        yield server


@pytest.fixture
def client():
    """登出端到端用例：SQLite 内存 user 表 + get_db 覆盖（无需 PG），Redis 走 fakeredis。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[User.__table__])
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def _override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    server = fakeredis.FakeStrictRedis(decode_responses=True)
    with (
        patch("app.core.token_revocation.get_redis", return_value=server),
        TestClient(app) as c,
    ):
        yield c
    app.dependency_overrides.clear()


def _bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def test_consume_token_fail_closed_when_redis_down():
    """S1：Redis 故障时 refresh 换发必须拒绝（fail-closed），防止并发重放无限续期。"""
    with patch("app.core.token_revocation.get_redis", side_effect=_redis_down):
        assert token_revocation.consume_token("jti-1", _FUTURE_EXP) is False


def test_is_revoked_fail_closed_when_redis_down():
    """S1：Redis 故障时 access 校验视为已吊销（fail-closed），防止已登出 token 存活。"""
    with patch("app.core.token_revocation.get_redis", side_effect=_redis_down):
        assert token_revocation.is_revoked("jti-1") is True


def test_revoke_token_best_effort_when_redis_down():
    """S1：登出写黑名单 best-effort —— Redis 故障不抛异常（不阻断登出接口）。"""
    with patch("app.core.token_revocation.get_redis", side_effect=_redis_down):
        token_revocation.revoke_token("jti-1", _FUTURE_EXP)  # 不应抛异常


def test_consume_token_success_and_replay(rdb):
    """正常路径（真 key 语义）：SETNX 首次成功 True、同 jti 复用 False、**不同 jti 不受影响**。

    旧 `_SeqRedis` 的第三条不可能成立（它按调用序返回，第二个键也被挡），故重放防护与
    键作用域在此同时钉住。
    """
    assert token_revocation.consume_token("jti-x", _FUTURE_EXP) is True
    assert token_revocation.consume_token("jti-x", _FUTURE_EXP) is False
    assert token_revocation.consume_token("jti-y", _FUTURE_EXP) is True


def test_revoke_blocks_only_that_jti(rdb):
    """A2 杀手断言：吊销 jti1 只让 jti1 失效，jti2 必须仍可用作 False。

    若 `is_revoked` 读的前缀与 `revoke_token` 写的前缀漂移（查错键），② 会立刻红：
    exists() 落到从未写入的命名空间 → 恒 False → 已吊销 token 复活。
    """
    token_revocation.revoke_token("jti-1", _FUTURE_EXP)
    assert token_revocation.is_revoked("jti-1") is True
    assert token_revocation.is_revoked("jti-2") is False


def test_revoke_writes_blacklisted_key_with_ttl(rdb):
    """黑名单键形状锁定：`revoked:<jti>` + 正 TTL（TTL=剩余有效期，到期自动回收）。"""
    token_revocation.revoke_token("jti-ttl", _FUTURE_EXP)
    assert rdb.exists("revoked:jti-ttl") == 1
    assert rdb.ttl("revoked:jti-ttl") > 0


def test_logout_revokes_access_token_end_to_end(client):
    """A2 缺口补齐：`POST /auth/logout` 此前**零测试**（grep logout tests/ 零命中）。

    登出后旧 access token 打 /auth/me 必须 401；同时未登出的其它 token 不受影响
    （反向钉住「查询键漂移」——漂移会让所有 token 一律 401 或一律 200）。
    """
    reg = client.post(
        f"{API}/auth/register", json={"email": "logout@b.com", "password": "secret123"}
    ).json()
    assert client.get(f"{API}/auth/me", headers=_bearer(reg["access_token"])).status_code == 200

    r = client.post(
        f"{API}/auth/logout", json={"refresh_token": reg["refresh_token"]},
        headers=_bearer(reg["access_token"]),
    )
    assert r.status_code == 200
    assert client.get(f"{API}/auth/me", headers=_bearer(reg["access_token"])).status_code == 401

    other = client.post(
        f"{API}/auth/register", json={"email": "other@b.com", "password": "secret123"}
    ).json()
    assert client.get(f"{API}/auth/me", headers=_bearer(other["access_token"])).status_code == 200


def test_logout_also_revokes_refresh_token(client):
    """登出同时吊销提交的 refresh token：旧 refresh 不能再换发（R-4 语义经登出路径成立）。"""
    reg = client.post(
        f"{API}/auth/register", json={"email": "rf@b.com", "password": "secret123"}
    ).json()
    client.post(
        f"{API}/auth/logout", json={"refresh_token": reg["refresh_token"]},
        headers=_bearer(reg["access_token"]),
    )
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": reg["refresh_token"]})
    assert r.status_code == 401


def test_empty_jti_bypass():
    """无 jti 的旧格式 token：无法防重放，放行（consume True / is_revoked False）。"""
    with patch("app.core.token_revocation.get_redis", side_effect=_redis_down):
        assert token_revocation.consume_token(None, _FUTURE_EXP) is True
        assert token_revocation.is_revoked(None) is False
