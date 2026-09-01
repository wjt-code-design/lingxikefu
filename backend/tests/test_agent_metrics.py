"""agent_metrics 单测（P2-2 降级指标，Batch3 覆盖率盲区）：计数/快照/排水日志。

模块为进程内计数（threading.Lock + defaultdict），测试前清空 _degraded_counts 防串扰。
"""
from __future__ import annotations

from app.services import agent_metrics as am


def setup_function(_):
    am._degraded_counts.clear()


def test_record_and_snapshot():
    am.record_degraded("image")
    am.record_degraded("image")
    am.record_degraded("ticket")
    assert am.snapshot() == {"image": 2, "ticket": 1}
    # snapshot 返回副本：改副本不影响内部计数
    snap = am.snapshot()
    snap["image"] = 99
    assert am.snapshot()["image"] == 2


def test_drain_degraded_counts_and_logs(caplog):
    with caplog.at_level("WARNING", logger="app.services.agent_metrics"):
        am.drain_degraded(["image", "ticket"], trace_id="t-1")
    assert am.snapshot() == {"image": 1, "ticket": 1}
    msgs = [r.getMessage() for r in caplog.records]
    assert any("tag=image" in m and "trace_id=t-1" in m for m in msgs)
    assert any("tag=ticket" in m and "trace_id=t-1" in m for m in msgs)


def test_drain_degraded_empty_noop():
    am.drain_degraded([], trace_id="t-2")
    assert am.snapshot() == {}
