"""多租户上下文：请求级 ContextVar 穿透。

设计：
- current_tenant：ContextVar，middleware 注入，全链路读取
- get_current_tenant()：优先读 ContextVar，未设置时回退 settings.TENANT_DEFAULT
  （兼容后台任务/脚本等无 HTTP 请求上下文的调用方）
- set_current_tenant(tenant)：供 middleware 或测试使用
"""
from __future__ import annotations

from contextvars import ContextVar

from app.core.config import settings

current_tenant: ContextVar[str] = ContextVar("current_tenant", default="")


def get_current_tenant() -> str:
    """获取当前请求租户 ID。

    优先级：
    1. ContextVar（HTTP 请求中间件注入）
    2. settings.TENANT_DEFAULT（后台任务/脚本兜底）
    """
    tenant = current_tenant.get()
    if tenant:
        return tenant
    return settings.TENANT_DEFAULT


def set_current_tenant(tenant: str) -> None:
    """设置当前请求租户 ID（供 middleware 或测试使用）。"""
    current_tenant.set(tenant)
