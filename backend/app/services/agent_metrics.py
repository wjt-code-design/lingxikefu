"""Agent 降级指标（P2-2，对抗审查 2026-08-27 落地）：降级留痕 → 结构化日志 + 进程内计数。

背景：``ctx.degraded`` 此前只进 logger.warning 文本，降级率（image 失败、ticket 建单
失败）无法统计，"降级禁止静默"（shared_context 约定）打折。
本模块：
- ``drain_degraded``：请求结束（chat 层 finally）把 ctx.degraded 逐条计数 + 打一条
  结构化日志（含 tag 与 trace_id），日志采集（loki/logstash）据此告警；
- ``snapshot``：进程内计数快照（单测用；多 worker 下跨实例聚合交给日志采集，
  不引入 Redis 依赖——与 telemetry 限流降级的内存路径同哲学）。
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_degraded_counts: dict[str, int] = defaultdict(int)


def record_degraded(tag: str) -> None:
    """进程内计数 +1（线程安全）。"""
    with _lock:
        _degraded_counts[tag] += 1


def snapshot() -> dict[str, int]:
    """当前累计计数快照（副本，测试/诊断用）。"""
    with _lock:
        return dict(_degraded_counts)


def drain_degraded(tags: list[str], *, trace_id: str = "") -> None:
    """请求结束时排水：逐条计数 + 结构化日志（告警路径 = 日志采集）。"""
    for tag in tags:
        record_degraded(tag)
        logger.warning("agent_degraded tag=%s trace_id=%s", tag, trace_id or "-")
