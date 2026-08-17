"""pytest 共享 fixture：为所有测试提供有效的最小环境变量。

保证 import app.* 时 `settings` 单例能通过 `validate()`
（test_health import app.main 会触发 fail-closed 启动校验）。

test_config.py 直接构造 Settings(**kwargs)，不依赖本环境变量。
"""
from __future__ import annotations

import os

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
