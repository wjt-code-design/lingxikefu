"""Quota 路由（BU-08 填充）：/api/v1/quota/me。"""
from fastapi import APIRouter

router = APIRouter(prefix="/quota", tags=["quota"])
