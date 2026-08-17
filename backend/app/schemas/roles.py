"""角色权限定义（/admin/roles 只读视图）：菜单级可见性 + 数据范围。

范围拍板（全端重构方案 v0.2 §6）：权限管理 = 菜单级可见性 + agent 数据范围，
按钮级 RBAC 后置 P2。角色定义是代码静态常量（单一真源），接口只读返回。
"""
from __future__ import annotations

from pydantic import BaseModel


class RoleDef(BaseModel):
    """单个角色的权限定义。"""

    role: str  # admin / agent / user
    name: str  # 显示名
    menus: list[str]  # 菜单级可见性：该角色可访问的菜单 key（与前端 SideNav 对齐）
    scope: str  # 数据范围：all=全租户 / agent_own=客服经手 / user_self=仅自己


class RoleListResp(BaseModel):
    """GET /admin/roles 响应。"""

    roles: list[RoleDef]
