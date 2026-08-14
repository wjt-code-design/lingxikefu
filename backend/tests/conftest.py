"""pytest 共享 fixture：为所有测试提供有效的最小环境变量。

强制赋值（而非 setdefault）：保证 import app.* 时 `settings` 单例能通过
`validate()`（test_health import app.main 会触发 fail-closed 启动校验），
同时避免宿主机环境变量里的占位值/非法值污染测试。

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
}

for _key, _value in _TEST_ENV.items():
    os.environ[_key] = _value
