"""共享 FastAPI 依赖的权限边界。"""
from __future__ import annotations

import pytest
from app.api.deps import require_roles
from fastapi import HTTPException


def test_require_roles_allows_staff_and_rejects_user():
    """staff-only 端点共用同一白名单，避免各 handler 的 403 逻辑漂移。"""
    checker = require_roles("admin", "agent")

    assert checker({"role": "admin"})["role"] == "admin"
    assert checker({"role": "agent"})["role"] == "agent"
    with pytest.raises(HTTPException) as exc:
        checker({"role": "user"})
    assert exc.value.status_code == 403
