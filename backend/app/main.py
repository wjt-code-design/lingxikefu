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
from contextlib import asynccontextmanager, suppress

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
    eval,
    faq,
    feedback,
    kb_publish,
    knowledge,
    knowledge_search,
    notifications,
    quota,
    roles,
    sessions,
    suggestions,
    telemetry,
    tickets,
)
from app.core.config import settings
from app.core.tracing import TraceIdFilter

logger = logging.getLogger("lingxi")


def _configure_logging() -> None:
    """最小日志配置：仅接管 lingxi logger（INFO+，带时间/级别/模块），不干扰 uvicorn。

    此前无任何配置 → root logger 默认 WARNING，RAG 管线 INFO（含预热/检索日志）全部不可见，
    生产排障只能靠 uvicorn 默认。此处只给 lingxi logger 挂 handler，避免影响 uvicorn 日志。
    P0-1：handler 挂 TraceIdFilter，日志统一带 [trace_id]（chat 请求可跨 RAG/Agent/工具追一条链）。
    """
    if not logger.handlers:  # 幂等：重复 import/重载不重复加 handler
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(trace_id)s] %(message)s"))
        _h.addFilter(TraceIdFilter())
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
    if not settings.RATE_LIMIT_ENABLED:
        return  # 测试/内部环境跳过（避免每用例起模型加载线程拖慢 TestClient）
    try:
        from app.llm_clients.embedding import get_embedding_client

        await asyncio.to_thread(get_embedding_client().embed, [""])
        logger.info("启动预热：embedding 模型已加载")
    except Exception:  # noqa: BLE001 - 预热失败不阻断启动
        logger.warning("启动预热失败（首个请求将触发懒加载）", exc_info=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期：启动恢复滞留导入 → 预热 embedding（避免首个用户等冷加载）→ 启动工单自动化调度。"""
    _recover_stale_imports()
    _recover_orphan_batches()
    # M4（外部审查 2026-08-29 核实）：create_task 仅持弱引用，保存引用防 GC 语义争议；
    # 关闭时取消并等待，避免关闭窗口内后台任务与解释器拆卸竞态（预热失败本就不阻断启动）。
    warmup_task = asyncio.create_task(_warmup_embedding())
    # 工单自动化：后台定时扫描超时工单
    try:
        from app.services.ticket_auto_scheduler import start_scheduler
        start_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("ticket_auto_scheduler: start failed (non-blocking)")
    yield
    # 关闭时停止调度器
    try:
        from app.services.ticket_auto_scheduler import stop_scheduler
        stop_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("ticket_auto_scheduler: stop failed (non-blocking)")
    # m1（bughunt-concurrency）：非守护线程池关停不排空——cancel_futures 丢弃排队任务，
    # 无界队列不再把解释器拆卸拖到分钟级（进行中任务 ≤25s LLM 由拆卸等待兜底）
    try:
        from app.services.agents.ticket_agent import shutdown_draft_pool
        shutdown_draft_pool()
    except Exception:  # noqa: BLE001
        logger.exception("ticket draft pool shutdown failed (non-blocking)")
    try:
        from app.services.intent_shadow import shutdown_shadow_pool
        shutdown_shadow_pool()
    except Exception:  # noqa: BLE001
        logger.exception("intent shadow pool shutdown failed (non-blocking)")
    # 关闭时取消预热任务（已完成则 cancel/await 均为无害空操作）
    warmup_task.cancel()
    with suppress(asyncio.CancelledError):
        await warmup_task
    # 共享 LLM AsyncClient 优雅关闭（2026-09-02 pitfall-sweep）：keep-alive 连接随进程
    # 拆卸由 GC 兜底本可接受，显式 aclose 释放 TCP/TLS 资源并避免解释器拆卸期告警。
    try:
        from app.llm_clients.chat import close_shared_client

        await close_shared_client()
    except Exception:  # noqa: BLE001
        logger.exception("shared chat client close failed (non-blocking)")


def _recover_stale_imports() -> None:
    """进程启动：把滞留在 parsing/embedding 的文档标 failed（见 knowledge_import_service）。

    daemon 导入线程随进程被强杀后文档会永久卡中间态 —— 重启用本钩子清理，幂等、不阻塞启动。

    - 测试/内部环境（RATE_LIMIT_ENABLED=false）直接跳过：不连 DB（TestClient 每用例起 app，
      若逐个连真实 PG 会让测试累计极慢）；生产默认 true 执行恢复。
    - 生产用**独立短超时 engine**（connect_timeout=2s）而非 SessionLocal：DB 不可达时快速失败，
      不阻塞 lifespan（防 pitfall G：启动钩子同步连 DB 无超时导致整体挂起）。
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.services.knowledge_import_service import recover_stale_imports

        eng = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
        try:
            db = Session(eng)
            try:
                n = recover_stale_imports(db)
                if n:
                    logger.info("启动恢复：将 %d 个滞留导入文档标记为 failed", n)
            finally:
                db.close()
        finally:
            eng.dispose()
    except Exception:  # noqa: BLE001 - 恢复失败不阻塞启动（懒加载兜底：下一请求仍会失败化）
        logger.warning("启动导入恢复失败（不影响启动）", exc_info=True)


def _recover_orphan_batches() -> None:
    """进程启动：把超时 evaluating 孤儿发布批次标 failed + 通知（bughunt M4）。

    快检 ~20min 窗口内进程重启/崩溃/兜底失败 → 批次永久卡 evaluating
    （publish 409、上传 400、列表永远「评测中」）。与 _recover_stale_imports
    同款纪律：独立短超时 engine、测试环境跳过、失败不阻塞启动。
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.services.kb_publish_service import recover_orphan_evaluating_batches

        eng = create_engine(settings.database_url, connect_args={"connect_timeout": 2})
        try:
            db = Session(eng)
            try:
                n = recover_orphan_evaluating_batches(db)
                if n:
                    logger.info("启动恢复：将 %d 个超时 evaluating 批次标记为 failed", n)
            finally:
                db.close()
        finally:
            eng.dispose()
    except Exception:  # noqa: BLE001 - 恢复失败不阻塞启动
        logger.warning("启动批次对账失败（不影响启动）", exc_info=True)


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


@app.middleware("http")
async def tenant_middleware(request: Request, call_next) -> Response:
    """多租户（P1-②）：恒设 ``settings.TENANT_DEFAULT``，注入 ContextVar 全链路穿透。

    修复前漏洞：中间件采信 ``X-Tenant-ID`` 头 / Host 子域名推断，而全仓鉴权读写
    硬编码 ``TENANT_DEFAULT``，唯 kb_lookup 走 `get_current_tenant()` 动态读 —— 同一
    请求内鉴权按 default、KB 查询按可伪造头，读写错位，伪造头可致跨租户数据泄漏。

    短期单租户正解：X-Tenant-ID 头 / Host 子域名一律不采信，租户恒为 TENANT_DEFAULT，
    与全局鉴权一致。多租户成为真需求时按 ADR 重做（届时加租户注册表白名单校验）。
    """
    from app.core.tenant import set_current_tenant

    tenant = settings.TENANT_DEFAULT
    set_current_tenant(tenant)
    response = await call_next(request)
    response.headers["X-Tenant-ID"] = tenant
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
    eval.router,
    kb_publish.router,
    faq.router,
    knowledge_search.router,
    notifications.router,
    roles.router,
    tickets.router,
    customers.router,
    suggestions.router,
    telemetry.router,
):
    app.include_router(_router, prefix=API_PREFIX)
