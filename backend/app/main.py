"""Lingxi Customer Service API —— 应用入口。

- 启动即 fail-closed：`settings.validate()`（红线⑨：配置单一真源 + 启动校验）。
- 启动预热：lifespan 线程池预加载本地 embedding 模型（冷启动首个请求检索 7s → 预热后 57ms）。
- `/health` 健康检查端点。
- CORS / 请求 ID 中间件。
- 挂载 API v1 路由分组（BU-02~09 逐个填充）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.api import (
    admin,
    admin_settings,
    audit_logs,
    auth,
    chat,
    customers,
    faq,
    feedback,
    knowledge,
    knowledge_search,
    quota,
    sessions,
    telemetry,
    tickets,
)
from app.core.config import settings

logger = logging.getLogger("lingxi")


def _configure_logging() -> None:
    """最小日志配置：仅接管 lingxi logger（INFO+，带时间/级别/模块），不干扰 uvicorn。

    此前无任何配置 → root logger 默认 WARNING，RAG 管线 INFO（含预热/检索日志）全部不可见，
    生产排障只能靠 uvicorn 默认。此处只给 lingxi logger 挂 handler，避免影响 uvicorn 日志。
    """
    if not logger.handlers:  # 幂等：重复 import/重载不重复加 handler
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(_h)
        logger.setLevel(logging.INFO)


_configure_logging()

API_PREFIX = "/api/v1"  # 与 contracts/api.ts 的 API_PREFIX 保持一致


# fail-closed 启动校验：任何配置缺失 / 占位 / 非法值在此抛 ValueError，进程拒绝启动。
settings.validate()

# L3：生产环境关闭 API 文档暴露（避免 schema 信息泄漏）；dev 保留便于调试
_app_kwargs: dict = {"title": "Lingxi Customer Service API", "version": "0.2"}
if settings.ENV == "prod":
    _app_kwargs["docs_url"] = None
    _app_kwargs["redoc_url"] = None


async def _warmup_embedding() -> None:
    """启动预热：预加载本地 embedding 模型（冷启动 ~7s → 热请求 57ms）。

    放线程池执行（model.encode 是 CPU 阻塞）；失败仅告警不阻塞启动——
    首个请求仍会触发懒加载，预热是尽力而为的体验优化。
    """
    try:
        from app.llm_clients.embedding import get_embedding_client

        await asyncio.to_thread(get_embedding_client().embed, [""])
        logger.info("启动预热：embedding 模型已加载")
    except Exception:  # noqa: BLE001 - 预热失败不阻断启动
        logger.warning("启动预热失败（首个请求将触发懒加载）", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期：启动预热 embedding（避免首个用户等冷加载）。"""
    asyncio.create_task(_warmup_embedding())
    yield


app = FastAPI(**_app_kwargs, lifespan=lifespan)


# L10 修复：BaseHTTPMiddleware → Starlette 原生 http 中间件（对 StreamingResponse 不缓冲，
# 避免 SSE 流式被额外包装的开销/潜在缓冲问题）
@app.middleware("http")
async def request_id_middleware(request: Request, call_next) -> Response:
    """为每个请求生成/透传 request_id，写入响应头（与统一错误模型 request_id 对齐）。"""
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# BUG-02（安全）：credentials 模式下禁用通配符方法/头——浏览器对 allow_credentials=True 的
# 通配符展开为反射模式，等于信任任意 Origin 的凭证请求。收窄到实际使用的白名单。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


# H1 修复：统一错误契约 {code, message, request_id}，让后端中文错误文案正确透传前端。
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": str(exc.status_code), "message": message, "request_id": _request_id(request)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    parts = []
    for err in exc.errors():
        loc = " → ".join(str(p) for p in err.get("loc", []) if p != "body")
        parts.append(f"{loc}: {err.get('msg', '')}" if loc else str(err.get("msg", "")))
    message = "；".join(p for p in parts if p) or "请求参数校验失败"
    return JSONResponse(
        status_code=422,
        content={"code": "422", "message": message, "request_id": _request_id(request)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error request_id=%s path=%s", _request_id(request), request.url.path)
    return JSONResponse(
        status_code=500,
        content={"code": "500", "message": "服务器内部错误，请稍后重试", "request_id": _request_id(request)},
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
    admin_settings.router,
    audit_logs.router,
    faq.router,
    knowledge_search.router,
    tickets.router,
    customers.router,
    telemetry.router,
):
    app.include_router(_router, prefix=API_PREFIX)
