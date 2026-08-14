"""Chat 路由（BU-06 填充）：/api/v1/chat/stream SSE 事件协议见 contracts/api.ts。"""
from fastapi import APIRouter

router = APIRouter(prefix="/chat", tags=["chat"])
