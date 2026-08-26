"""P0-1 trace_id 全链路：日志 Filter + SharedContext 字段（可观测性底座）。

- TraceIdFilter：把 contextvar 中的 trace_id 附加到日志记录（不逐行改 logger）；
- SharedContext.trace_id：chat 层注入，Router/各 Agent 可读（权限表已登记）。
"""
from __future__ import annotations

import io
import logging

from app.core.tracing import TraceIdFilter, set_trace_id
from app.services.shared_context import SharedContext


def test_log_filter_attaches_trace_id():
    """TraceIdFilter 把 contextvar 中的 trace_id 附加到日志记录。"""
    stream = io.StringIO()
    h = logging.StreamHandler(stream)
    h.setFormatter(logging.Formatter("%(trace_id)s|%(message)s"))
    h.addFilter(TraceIdFilter())
    lg = logging.getLogger("test_trace_id_filter")
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    set_trace_id("abc123")
    try:
        lg.info("hello")
    finally:
        lg.handlers = []
        set_trace_id("")
    out = stream.getvalue().strip()
    assert out == "abc123|hello"


def test_log_filter_empty_when_unset():
    """未设置 trace_id 时（非 chat 请求），日志 trace_id 为空串不报错。"""
    stream = io.StringIO()
    h = logging.StreamHandler(stream)
    h.setFormatter(logging.Formatter("[%(trace_id)s] %(message)s"))
    h.addFilter(TraceIdFilter())
    lg = logging.getLogger("test_trace_id_empty")
    lg.handlers = [h]
    lg.propagate = False
    lg.setLevel(logging.INFO)
    set_trace_id("")
    try:
        lg.info("hi")
    finally:
        lg.handlers = []
    assert stream.getvalue().strip() == "[] hi"


def test_shared_context_has_trace_id_field():
    """SharedContext 携带 trace_id（chat 层写入，Router/各 Agent 可读）。"""
    assert SharedContext().trace_id == ""
    assert SharedContext(trace_id="tid-1").trace_id == "tid-1"
