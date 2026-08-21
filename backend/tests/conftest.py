"""pytest 共享 fixture：为所有测试提供有效的最小环境变量。

保证 import app.* 时 `settings` 单例能通过 `validate()`
（test_health import app.main 会触发 fail-closed 启动校验）。

test_config.py 直接构造 Settings(**kwargs)，不依赖本环境变量。

Redis 兜底：API 测试隐式依赖 Redis（is_revoked fail-closed / quota / telemetry）。
本地无 Redis 时全部 401——用 fakeredis 全局替换（测试自洽，不依赖外部服务）；
测试内部自带 patch 的用例（test_token_revocation / test_quota）优先生效，互不冲突。
"""
from __future__ import annotations

import os

import pytest

_TEST_ENV: dict[str, str] = {
    "JWT_SECRET": "unit-test-secret",
    "LITELLM_MASTER_KEY": "unit-test-litellm-key",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USER": "lingxi",
    "POSTGRES_PASSWORD": "lingxi",
    "POSTGRES_DB": "lingxi",
    "REDIS_URL": "redis://localhost:6379/0",
    "QDRANT_URL": "http://localhost:6333",
    # 测试环境关闭登录/注册限流：TestClient 共享同一 IP，用例间累计会误伤
    "RATE_LIMIT_ENABLED": "false",
    # 测试环境关闭答案缓存：避免单测触发真实 Qdrant 建集合 + 本地 embedding 加载
    "ANSWER_CACHE_ENABLED": "false",
}

# 用 setdefault（而非强制覆盖）：优先尊重外部显式注入的连接配置
# （例如完整环境验证时注入真实 POSTGRES_PASSWORD），默认值保持测试自洽。
for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

#: from-import 拿走 get_redis 引用的全部模块（patch 需逐模块打到各自命名空间）
_REDIS_PATCH_TARGETS = (
    "app.core.token_revocation.get_redis",
    "app.core.rate_limit.get_redis",
    "app.services.answer_cache.get_redis",
    "app.api.telemetry.get_redis",
    "app.services.quota.get_redis",
    "app.services.user_profile_service.get_redis",  # 2026-08-22 Phase B：画像幂等键查重
)


@pytest.fixture(autouse=True, scope="session")
def _fake_redis():
    """session 级 fakeredis 兜底：本地/CI 无真实 Redis 也能跑全量测试。

    fail-closed 语义（Redis 故障拒绝）已由 test_token_revocation 内部 patch 专门覆盖，
    此处只保证"正常路径"测试不因环境缺 Redis 而全量 401。
    """
    try:
        import fakeredis
    except ImportError:  # fakeredis 未安装：退回真实 Redis（容器 CI 场景）
        yield
        return
    server = fakeredis.FakeStrictRedis(decode_responses=True)
    from unittest.mock import patch

    patches = [patch(t, return_value=server) for t in _REDIS_PATCH_TARGETS]
    for p in patches:
        p.start()
    yield server
    for p in patches:
        p.stop()
