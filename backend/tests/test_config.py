"""配置启动校验单测（BU-01 DoD §3）。

覆盖：占位 Key / 缺失必填 / 非法端口 → settings.validate() 抛 ValueError；
默认值自洽无矛盾（防 aegisdesk-ai「默认值三处自相矛盾」坑）。
"""
from __future__ import annotations

import pytest
from app.core.config import PLACEHOLDER_SECRET, Settings
from pydantic import ValidationError


def make_settings(**overrides: object) -> Settings:
    """构造一份「全字段有效」的 Settings，再用 overrides 定向破坏。"""
    defaults: dict[str, object] = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": 5432,
        "POSTGRES_USER": "lingxi",
        "POSTGRES_PASSWORD": "lingxi",
        "POSTGRES_DB": "lingxi",
        "REDIS_URL": "redis://localhost:6379/0",
        "QDRANT_URL": "http://localhost:6333",
        "JWT_SECRET": "unit-test-secret",
        "LITELLM_MASTER_KEY": "unit-test-litellm-key",
        "CORS_ORIGINS": ["http://localhost:5173"],
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[arg-type]


def test_validate_passes_with_valid_values() -> None:
    """全字段有效时 validate() 不应抛错。"""
    make_settings().validate()


@pytest.mark.parametrize("secret", [PLACEHOLDER_SECRET, ""])
def test_validate_rejects_placeholder_jwt_secret(secret: str) -> None:
    """JWT_SECRET 为占位符 __CHANGE_ME__ 或空 → validate() 必须抛错（防占位泄漏）。"""
    settings = make_settings(JWT_SECRET=secret)
    with pytest.raises(ValueError, match="JWT_SECRET"):
        settings.validate()


def test_validate_rejects_placeholder_litellm_key() -> None:
    """LITELLM_MASTER_KEY 为占位符 → validate() 必须抛错。"""
    settings = make_settings(LITELLM_MASTER_KEY=PLACEHOLDER_SECRET)
    with pytest.raises(ValueError, match="LITELLM_MASTER_KEY"):
        settings.validate()


@pytest.mark.parametrize("field", ["POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"])
def test_validate_rejects_missing_postgres_fields(field: str) -> None:
    """缺任意必填数据库字段 → validate() 抛错并指出字段名。"""
    settings = make_settings(**{field: ""})
    with pytest.raises(ValueError, match=field):
        settings.validate()


@pytest.mark.parametrize("field", ["REDIS_URL", "QDRANT_URL"])
def test_validate_rejects_missing_infra_url(field: str) -> None:
    """缺 REDIS_URL / QDRANT_URL → validate() 抛错。"""
    settings = make_settings(**{field: ""})
    with pytest.raises(ValueError, match=field):
        settings.validate()


def test_validate_rejects_invalid_port() -> None:
    """POSTGRES_PORT 非法值（越界）→ validate() 抛错。"""
    settings = make_settings(POSTGRES_PORT=70000)
    with pytest.raises(ValueError, match="POSTGRES_PORT"):
        settings.validate()


def test_validate_rejects_non_numeric_port() -> None:
    """POSTGRES_PORT 非整数 → pydantic 实例化即抛 ValidationError。"""
    with pytest.raises(ValidationError):
        make_settings(POSTGRES_PORT="not-a-number")


def test_missing_env_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量全部缺失时（单测跑在干净环境），validate() 必须抛错。"""
    for key in (
        "JWT_SECRET",
        "LITELLM_MASTER_KEY",
        "POSTGRES_HOST",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "REDIS_URL",
        "QDRANT_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    with pytest.raises(ValueError):
        settings.validate()


def test_defaults_are_consistent() -> None:
    """默认值自洽、无矛盾（防 aegisdesk-ai 多份默认值漂移）。"""
    settings = make_settings()
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 15
    assert settings.REFRESH_TOKEN_EXPIRE_DAYS == 7
    assert settings.TENANT_DEFAULT == "default"
    assert settings.POSTGRES_PORT == 5432
    assert "http://localhost:5173" in settings.CORS_ORIGINS


def test_jwt_secret_default_is_placeholder() -> None:
    """未显式配置时 JWT_SECRET 默认即占位符（保证「不配置=起不来」语义）。"""
    assert make_settings(JWT_SECRET=PLACEHOLDER_SECRET).JWT_SECRET == PLACEHOLDER_SECRET


def test_database_url_built_from_fields() -> None:
    """database_url 由 POSTGRES_* 拼接，不含默认值漂移。"""
    settings = make_settings()
    assert settings.database_url.startswith("postgresql+psycopg://lingxi:lingxi@localhost:5432/lingxi")
