"""B2-5：弃 passlib、直接 bcrypt 的回归 + 红测（2026-09-06 深度审查）。

背景：passlib 1.7.4 通过 `bcrypt.__about__.__version__` 探测后端版本，该属性在
bcrypt 4.1+ 已移除（4.1.0 因此被 PyPI yank）——每次 hash/verify（即每次登录/注册）
内部触发 AttributeError→PasslibHashWarning。passlib 2020 后停维护。根治=直接
bcrypt.hashpw/checkpw（$2b$ 格式与存量哈希兼容，零迁移）。

红测（切换前必红）：
- test_security_no_passlib：源码不再 import passlib / 用 CryptContext。
- test_register_rejects_overlong_password：RegisterReq 加 max_length 后超长 422。
回归保护（切换前后都绿，防 bcrypt 切换破坏存量哈希可验证性）：
- test_verify_password_compatible_with_passlib_era_hash：passlib 时代 $2b$ 哈希仍可校验。
"""
from __future__ import annotations

import inspect

import app.core.security as security
import app.models.user  # noqa: F401  注册 User 表
import pytest
from app.core.database import get_db
from app.core.security import hash_password, verify_password
from app.main import app
from app.models.base import Base
from app.models.user import User
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

API = "/api/v1"

# passlib 1.7.4 在本机实跑产出的真实哈希（明文 SmokePass123），代表存量库里的历史哈希格式
PASSLIB_ERA_HASH = "$2b$12$6x9WxjOgD.rQ2AMkQj/nFuEsPn/o5SiIxQNgTTkd7GYNjoV8wjkZW"
PASSLIB_ERA_PLAIN = "SmokePass123"


def test_security_no_passlib():
    """B2-5 根治：security 模块不得再 import passlib / 实例化 CryptContext（每次认证触发
    AttributeError 的根因）。按 import 语句与调用检查，不匹配说明性注释里的 "passlib" 字样。
    """
    src = inspect.getsource(security)
    assert "import passlib" not in src, "security 仍 import passlib"
    assert "from passlib" not in src, "security 仍 from passlib import"
    assert "CryptContext(" not in src, "security 仍实例化 CryptContext（应改直接 bcrypt）"
    # 运行时：模块命名空间不应有 passlib 痕迹
    assert not any(name.startswith("passlib") for name in dir(security))


def test_verify_password_compatible_with_passlib_era_hash():
    """存量兼容：passlib 时代产出的 $2b$ 哈希，切换后 verify_password 仍须校验通过（零迁移前提）。"""
    assert verify_password(PASSLIB_ERA_PLAIN, PASSLIB_ERA_HASH) is True
    assert verify_password("wrong-password", PASSLIB_ERA_HASH) is False


def test_hash_password_roundtrip_and_format():
    """新哈希：bcrypt 格式（$2 前缀）+ 自校验往返。"""
    h = hash_password("FreshPass123")
    assert h.startswith("$2")
    assert verify_password("FreshPass123", h) is True
    assert verify_password("FreshPass124", h) is False


def test_verify_password_malformed_hash_returns_false():
    """非法/损坏哈希串：verify_password 返回 False 不抛（fail-safe，不 500 认证链路）。"""
    assert verify_password("anything", "not-a-hash") is False
    assert verify_password("anything", "") is False


@pytest.fixture
def client():
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
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_register_rejects_overlong_password(client):
    """B2-5 边界：bcrypt 静默截断 72 字节 → 超长口令存在「前 72 字节相同即同密码」等价类。
    RegisterReq 加 max_length=72 后，>72 字节密码必须 422 拒绝（切换前无上限 → 201 → 红）。
    """
    long_pwd = "Aa1" + "x" * 100  # 含字母数字、长度 >72
    r = client.post(f"{API}/auth/register", json={"email": "long@b.com", "password": long_pwd})
    assert r.status_code == 422, f"超长密码应被拒: HTTP {r.status_code} {r.text}"
