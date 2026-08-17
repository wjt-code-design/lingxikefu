"""Admin Roles API 测试：/admin/roles 只读角色权限定义 + 权限控制。"""
from __future__ import annotations

import uuid

import pytest
from app.core.security import create_access_token
from app.main import app
from fastapi.testclient import TestClient

API = "/api/v1"

ADMIN = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
AGENT = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _h(uid: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(subject=str(uid), role=role)}"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_roles_admin_access(client):
    """admin：200 + 三角色齐全 + 菜单级可见性 / 数据范围符合拍板契约。"""
    r = client.get(f"{API}/admin/roles", headers=_h(ADMIN, "admin"))
    assert r.status_code == 200
    data = r.json()
    roles = {d["role"]: d for d in data["roles"]}
    assert set(roles) == {"admin", "agent", "user"}
    # 数据范围（菜单级 + agent 数据范围）
    assert roles["admin"]["scope"] == "all"
    assert roles["agent"]["scope"] == "agent_own"
    assert roles["user"]["scope"] == "user_self"
    # 菜单级可见性：admin 含 admin+agent 菜单；user 仅自身菜单
    assert "/admin/roles" in roles["admin"]["menus"]
    assert "/agent/dashboard" in roles["agent"]["menus"]
    assert "/chat" in roles["user"]["menus"]
    assert "/admin/users" not in roles["user"]["menus"]


def test_roles_forbidden_for_user(client):
    """非 admin（user）→ 403。"""
    r = client.get(f"{API}/admin/roles", headers=_h(USER, "user"))
    assert r.status_code == 403


def test_roles_forbidden_for_agent(client):
    """非 admin（agent）→ 403。"""
    r = client.get(f"{API}/admin/roles", headers=_h(AGENT, "agent"))
    assert r.status_code == 403
