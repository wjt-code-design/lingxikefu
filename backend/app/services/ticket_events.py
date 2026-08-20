"""用户侧工单状态事件总线（第6组项4）：进程内队列 + publish + SSE 消费。

设计（对齐通知中心的进程内队列模式，但按 user_id 维度分发）：
- ``publish``：工单状态变更 → 入队（尽力而为，队满丢弃——前端轮询兜底，不阻塞主请求）；
- ``_sse_gen(user_id)``：仅吐出属于该用户的事件（connected/ping 心跳/ticket_update）。

已知限制（诚实标注）：
- **单 worker 进程内**：多 worker 部署下，事件可能落在持有 SSE 连接的 worker 之外 → 推送不是强一致；
  前端保留 30s 轮询兜底，保证最终一致（推送尽力而为，轮询保底）。
"""
from __future__ import annotations

import json
import queue
from datetime import datetime, timezone

#: 进程内工单事件队列（容量有限，防积压）
_ticket_queue: queue.Queue = queue.Queue(maxsize=200)
#: SSE 心跳间隔（秒）：防代理/网关静默断连（与通知中心一致）
_HEARTBEAT_INTERVAL = 15


def publish_ticket_event(user_id: str, ticket_id: str, status: str) -> None:
    """发布工单状态事件（尽力而为；队满/多 worker 不在本 worker 时丢弃，前端轮询兜底）。"""
    item = {
        "user_id": user_id,
        "ticket_id": ticket_id,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _ticket_queue.put_nowait(item)
    except queue.Full:
        pass  # 队满丢弃（不阻塞：推送是尽力而为）


def _matches_user(item: dict | None, user_id: str) -> bool:
    """事件是否属于该用户（独立函数便于单测分区隔离，不触发 15s 心跳等待）。"""
    return item is not None and item.get("user_id") == user_id


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sse_gen(user_id: str):
    """SSE 事件生成器（独立函数便于单测）：connected → 心跳 ping / 当前用户工单事件。"""
    import asyncio

    yield _sse({"event": "connected", "data": {"user_id": user_id}})
    while True:
        try:
            item = await asyncio.to_thread(_ticket_queue.get, timeout=_HEARTBEAT_INTERVAL)
        except queue.Empty:
            yield _sse({"event": "ping", "data": {"ts": datetime.now(timezone.utc).isoformat()}})
            continue
        if _matches_user(item, user_id):
            yield _sse({"event": "ticket_update", "data": item})