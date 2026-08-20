"""用户侧工单事件总线测试（第6组项4）：publish → per-user SSE 过滤。

- own user 的 gen 收到 ticket_update（含 ticket_id/status）；
- 其他用户取不到（_matches_user 分区隔离，避免等待 15s 心跳才验证）；
- 首帧 connected 握手。
跨测试隔离：每个用例前清空进程内队列（防 A-单例泄漏到下一用例）。
"""
from __future__ import annotations

import pytest

from app.services import ticket_events
from app.services.ticket_events import _matches_user, _sse_gen, publish_ticket_event


@pytest.fixture(autouse=True)
def clean_queue():
    ticket_events._ticket_queue.queue.clear()
    yield


async def _collect(agen, n: int) -> list[str]:
    """取 async generator 前 n 帧（不足则提前结束）。"""
    out: list[str] = []
    it = agen.__aiter__()
    for _ in range(n):
        try:
            out.append(await it.__anext__())
        except StopAsyncIteration:
            break
    return out


async def test_publish_delivers_to_owner_user():
    """自己的事件：connected 后收到 ticket_update（含状态）。"""
    publish_ticket_event("u1", "t-abc", "processing")
    frames = await _collect(_sse_gen("u1"), 2)  # 事件已入队，前两帧无心跳等待
    joined = "\n".join(frames)
    assert '"event": "connected"' in joined
    assert '"event": "ticket_update"' in joined
    assert '"ticket_id": "t-abc"' in joined
    assert '"status": "processing"' in joined


async def test_connected_first_frame():
    frames = await _collect(_sse_gen("u7"), 1)
    assert frames[0].startswith("data: ") and '"event": "connected"' in frames[0]


def test_matches_user_isolates_by_user():
    """分区隔离：u1 的事件只匹配 u1，不匹配 u2/None。"""
    publish_ticket_event("u1", "t1", "resolved")
    item = ticket_events._ticket_queue.get_nowait()
    assert _matches_user(item, "u1") is True
    assert _matches_user(item, "u2") is False
    assert _matches_user(None, "u1") is False