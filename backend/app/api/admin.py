"""Admin 路由（BU-09 填充）：/api/v1/admin/users|stats（真实查询，禁空壳）。"""
from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])
