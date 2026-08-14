"""Auth 路由（BU-02 填充）：/api/v1/auth/register|login|refresh|logout|me。"""
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
