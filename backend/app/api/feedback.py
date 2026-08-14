"""Feedback 路由（BU-07 填充）：/api/v1/messages/{message_id}/feedback。"""
from fastapi import APIRouter

router = APIRouter(prefix="/messages", tags=["feedback"])
