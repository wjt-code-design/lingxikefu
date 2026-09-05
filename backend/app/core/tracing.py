"""P0-1 trace_id 全链路追踪：请求级 trace_id 的 contextvar + 日志 Filter。

- chat_stream 入口把 HTTP 中间件生成的 request_id 写入 trace_var（contextvar，
  asyncio 自动传播到同请求的协程；anyio 线程池 worker 会复制 context，因此
  run_in_threadpool 内的 RAG 阻塞段日志同样带 trace_id）；
- TraceIdFilter 挂在 lingxi logger 的 handler 上，为每条日志记录附加 trace_id
  字段，让 RAG / Agent / 工具 / 落库的日志统一带 trace_id，不必逐行改 logger 调用。
"""
from __future__ import annotations

import contextvars
import logging

#: 请求级 trace_id（缺省空串 = 非 chat 请求，如健康检查/鉴权，日志 trace_id 为空）
trace_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def set_trace_id(tid: str) -> None:
    """设置当前请求的 trace_id（chat_stream 入口调用一次即可）。"""
    trace_var.set(tid or "")


class TraceIdFilter(logging.Filter):
    """日志 Filter：把 contextvar 中的 trace_id 附加到每条日志记录。

    挂在 lingxi logger 的 handler 上；格式化串用 %(trace_id)s 读取。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_var.get() or ""
        return True
