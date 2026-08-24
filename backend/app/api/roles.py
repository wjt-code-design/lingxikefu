"""Admin 角色权限（C9 / 方案 v0.2 §6）：GET /admin/roles 只读返回角色权限定义。

范围：菜单级可见性 + agent 数据范围；按钮级 RBAC 后置 P2（方案已拍板）。
角色定义为代码静态常量（单一真源），menus 与前端 SideNav 菜单清单对齐。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.schemas.roles import RoleDef, RoleListResp

router = APIRouter(prefix="/admin", tags=["admin"])

#: 角色-权限静态定义（单一真源）：menus 与 frontend SideNav 菜单清单保持一致
ROLE_DEFS: list[RoleDef] = [
    RoleDef(
        role="admin",
        name="管理员",
        menus=[
            "/admin/dashboard", "/admin/knowledge", "/admin/users", "/admin/stats",
            "/admin/feedback", "/admin/sessions", "/admin/settings", "/admin/logs",
            "/admin/roles", "/admin/eval", "/agent/dashboard", "/agent/sessions",
            "/agent/tickets", "/agent/customers", "/agent/kb-search", "/chat",
        ],
        scope="all",
    ),
    RoleDef(
        role="agent",
        name="客服",
        menus=[
            "/agent/dashboard", "/agent/sessions", "/agent/tickets",
            "/agent/customers", "/agent/kb-search", "/chat",
        ],
        scope="agent_own",
    ),
    RoleDef(
        role="user",
        name="用户",
        menus=["/chat", "/tickets", "/faq", "/help"],
        scope="user_self",
    ),
]


@router.get("/roles", response_model=RoleListResp)
def list_roles(_: dict = Depends(require_admin)) -> RoleListResp:
    """返回角色权限定义（菜单级可见性 + 数据范围），仅 admin 可读。"""
    return RoleListResp(roles=ROLE_DEFS)
