"""前端可观测上报（ErrorBoundary TODO 落地）：POST /telemetry/frontend-error。

- 前端错误边界捕获异常后 sendBeacon 上报（fire-and-forget，页面卸载时也能送达）；
- 后端仅记结构化日志（request_id 由 RequestIDMiddleware 注入），不落库、无额外依赖；
- 权限：匿名可访问（登录页/挂件页异常同样可上报）；
- 防御：Redis 固定窗口限流（多 worker 共享，防日志刷屏 DoS）+ 日志注入转义（message/stack JSON 编码，破坏换行伪造）。
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.redis_client import get_redis

logger = logging.getLogger("lingxi")

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

#: 固定窗口限流：每 IP 60s 最多 10 条。Redis 优先（多 worker 共享计数），
#: Redis 不可用时降级到进程内滑动窗口（保留单进程防护，量级极低可接受）。
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 10
_RATE_LIMIT_KEY_PREFIX = "telemetry:rl:"
#: 降级路径：内存滑动窗口（单 worker 防护）
_recent: dict[str, deque[float]] = defaultdict(deque)


class FrontendErrorReq(BaseModel):
    message: str = Field(default="", max_length=2000)
    stack: str = Field(default="", max_length=8000)
    component: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=2000)
    user_agent: str = Field(default="", max_length=500)


def _limited_memory(client_ip: str) -> bool:
    """进程内滑动窗口降级限流：窗口外旧记录清理后判断。"""
    now = time.monotonic()
    dq = _recent[client_ip]
    while dq and now - dq[0] > _RATE_LIMIT_WINDOW:
        dq.popleft()
    if len(dq) >= _RATE_LIMIT_MAX:
        return True
    dq.append(now)
    return False


def _limited(client_ip: str) -> bool:
    """固定窗口限流：优先 Redis（多 worker 共享），Redis 不可用降级内存。"""
    try:
        r = get_redis()
        key = _RATE_LIMIT_KEY_PREFIX + client_ip
        count = r.incr(key)
        if count == 1:
            r.expire(key, _RATE_LIMIT_WINDOW)
        return count > _RATE_LIMIT_MAX
    except Exception:  # noqa: BLE001 - Redis 不可用：降级内存滑动窗口
        logger.warning("telemetry 限流: redis 不可用，降级内存窗口")
        return _limited_memory(client_ip)


@router.post("/frontend-error", status_code=204)
def report_frontend_error(body: FrontendErrorReq, request: Request) -> None:
    """接收前端错误边界上报，写结构化日志（含 request_id，便于排障串联）。

    纯日志端点，不落库、不依赖 DB（错误上报不应因 DB 故障而失败）。
    超限请求静默丢弃（204 返回，不暴露限流语义）。
    """
    ip = request.client.host if request.client else "-"
    if _limited(ip):
        return
    # JSON 编码 message/stack：破坏换行/控制字符，防日志行伪造（review 🟡4）
    logger.error(
        "frontend_error request_id=%s component=%s url=%s ua=%s message=%s stack=%s",
        getattr(request.state, "request_id", ""),
        body.component or "-",
        body.url or "-",
        body.user_agent or "-",
        json.dumps(body.message or "-", ensure_ascii=False),
        json.dumps((body.stack or "-")[:500], ensure_ascii=False),
    )
