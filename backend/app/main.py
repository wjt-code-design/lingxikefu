"""Lingxi Customer Service API —— 应用入口。

- 启动即 fail-closed：`settings.validate()`（红线⑨：配置单一真源 + 启动校验）。
- `/health` 健康检查端点。
- CORS / 请求 ID 中间件。
- 挂载 API v1 路由分组（BU-02~09 逐个填充）。
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api import admin, auth, chat, feedback, knowledge, quota, sessions
from app.core.config import settings

API_PREFIX = "/api/v1"  # 与 contracts/api.ts 的 API_PREFIX 保持一致


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个请求生成/透传 request_id，写入响应头（与统一错误模型 request_id 对齐）。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# fail-closed 启动校验：任何配置缺失 / 占位 / 非法值在此抛 ValueError，进程拒绝启动。
settings.validate()

app = FastAPI(
    title="Lingxi Customer Service API",
    version="0.2",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
def health() -> dict:
    """健康检查：返回 ok 与当前租户。"""
    return {"status": "ok", "tenant": settings.TENANT_DEFAULT}


for _router in (
    auth.router,
    sessions.router,
    chat.router,
    knowledge.router,
    knowledge.documents_router,
    feedback.router,
    quota.router,
    admin.router,
):
    app.include_router(_router, prefix=API_PREFIX)
