"""前端可观测上报（ErrorBoundary TODO 落地）：POST /telemetry/frontend-error。

- 前端错误边界捕获异常后 sendBeacon 上报（fire-and-forget，页面卸载时也能送达）；
- 后端仅记结构化日志（request_id 由 RequestIDMiddleware 注入），不落库、无额外依赖；
- 权限：匿名可访问（登录页/挂件页异常同样可上报）；
- 防御：Redis 固定窗口限流（多 worker 共享，防日志刷屏 DoS）+ 日志注入转义（message/stack JSON 编码，破坏换行伪造）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque

from cachetools import TTLCache
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
#: 降级路径：内存滑动窗口（单 worker 防护）。
#: B7（审计 2026-09-27，对齐 P3-⑩）：容器换成有界 TTLCache——原 defaultdict(deque) 只
#: popleft 不删 key，每个见过的 IP（本端点匿名可访问）永久留一条记录 ⇒ 无界增长。
_recent: TTLCache = TTLCache(maxsize=1024, ttl=_RATE_LIMIT_WINDOW)
#: TTLCache 非线程安全且本端点跑在线程池里（同步 def），与 P3-⑩ 的 _suggest_cache 同款显式加锁
_recent_lock = threading.Lock()


class FrontendErrorReq(BaseModel):
    message: str = Field(default="", max_length=2000)
    stack: str = Field(default="", max_length=8000)
    component: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=2000)
    user_agent: str = Field(default="", max_length=500)


def _limited_memory(client_ip: str) -> bool:
    """进程内滑动窗口降级限流：窗口外旧记录清理后判断。"""
    now = time.monotonic()
    with _recent_lock:
        dq = _recent.get(client_ip, deque())
        while dq and now - dq[0] > _RATE_LIMIT_WINDOW:
            dq.popleft()
        limited = len(dq) >= _RATE_LIMIT_MAX
        if not limited:
            dq.append(now)
        # 写回即重置该 IP 的 TTL：活跃者保持计数，闲置满一个窗口后整条被淘汰（B7 的有界性来源）
        _recent[client_ip] = dq
        return limited


def _limited(client_ip: str) -> bool:
    """固定窗口限流：优先 Redis（多 worker 共享），Redis 不可用降级内存。"""
    try:
        r = get_redis()
        key = _RATE_LIMIT_KEY_PREFIX + client_ip
        # B4（审计 2026-09-27，同款手法见 rate_limit.py 的 P3-⑪ / B3）：建键带 TTL 与计数并入
        # 同一 MULTI pipeline 原子提交。此前是 incr + 条件 expire 两条独立命令，中间进程被杀就
        # 留下永不过期的计数键 ⇒ 该 IP 从此上报不上（响应恒 204，前端无感、无人报错）。
        # SET NX 只在窗口开启（键不存在）那次落 TTL，故被拒请求不会把窗口顶回满值。
        pipe = r.pipeline()
        pipe.set(key, 0, nx=True, ex=_RATE_LIMIT_WINDOW)
        pipe.incr(key)
        results = pipe.execute()
        return int(results[1]) > _RATE_LIMIT_MAX
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
