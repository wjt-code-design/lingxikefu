"""启动钩子真调用冒烟（A7 / 审计 M4 2026-09-27）。

上一批（B2）把 `app/main.py` 的三个启动钩子（导入恢复 / 孤儿批次恢复 / embedding 预热）
从 `RATE_LIMIT_ENABLED` 解耦到独立开关 `STARTUP_DB_HOOKS_ENABLED`。**但当时没有任何尺子
守着这条解耦**：谁把三处判据改回 `RATE_LIMIT_ENABLED`，全量测试一条都不会红（B2 的
症状——生产关限流连带静默停掉这三件事——就会复发）。

本文件真起 app（TestClient 进 lifespan），把三个钩子的下游协作方打成记录器：
开关为 True → 三件事都发生；为 False → 一件都不发生。DB 不必真起（钩子用独立短超时
engine，且恢复函数已被打成记录器，不产生连接）。

关停段同样由本文件守（`test_lifespan_shutdown_releases_every_resource`）——原
`test_llm_clients.py::test_lifespan_shutdown_calls_close` 那条文本守卫删除后，关停侧
"哪一步被注释掉都不会红"的缺口需要行为断言补回，而不是留成裸奔。
"""
from __future__ import annotations

import time

import app.llm_clients.chat as chat_mod
import app.llm_clients.embedding as embedding_mod
import app.llm_clients.volcengine_vision as vision_mod
import app.main as m
import app.services.agents.ticket_agent as ticket_agent_mod
import app.services.intent_shadow as shadow_mod
import app.services.kb_publish_service as kb_pub
import app.services.knowledge_import_service as ki_svc
import app.services.ticket_auto_scheduler as sched_mod
from fastapi.testclient import TestClient

app = m.app


class _Recorder:
    """可调用记录器：记次数与最后一次入参，返回值为被测代码所需形状。"""

    def __init__(self, ret=None):
        self.calls: list[tuple] = []
        self.ret = ret

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


class _AsyncRecorder(_Recorder):
    """协程版记录器：lifespan 关闭段的 close_shared_client 是被 await 的。"""

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.ret


class _Spy:
    """透传探针：记调用的同时执行真实现（关停只桩不断=假守卫，且会漏停线程污染同 session 用例）。"""

    def __init__(self, real):
        self.real = real
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.real(*args, **kwargs)


class _AsyncSpy(_Spy):
    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return await self.real(*args, **kwargs)


def _wait_until(pred, timeout: float = 3.0) -> bool:
    """有界轮询：预热钩子在 asyncio.to_thread 的 worker 线程里落记录器，
    TestClient 的事件循环跑在另一线程 → 主线程需要给它时间片（非重试，是跨线程等待）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def _arm(monkeypatch):
    """把三个钩子的下游 + lifespan 关闭期的共享 client 关闭打成记录器。"""
    rec_imports, rec_batches = _Recorder(0), _Recorder(0)
    rec_embed, rec_close = _Recorder([]), _AsyncRecorder()
    monkeypatch.setattr(ki_svc, "recover_stale_imports", rec_imports)
    monkeypatch.setattr(kb_pub, "recover_orphan_evaluating_batches", rec_batches)

    class _StubEmbedding:
        dim = 8

        def embed(self, texts):
            rec_embed(texts)
            return [[0.0] * self.dim for _ in texts]

    monkeypatch.setattr(embedding_mod, "get_embedding_client", lambda: _StubEmbedding())
    monkeypatch.setattr(chat_mod, "close_shared_client", rec_close)
    return rec_imports, rec_batches, rec_embed, rec_close


def test_startup_hooks_run_when_enabled(monkeypatch):
    """STARTUP_DB_HOOKS_ENABLED=True → 两个恢复钩子 + embedding 预热都真被调用。

    被守住的失效形态：三处判据被改回 RATE_LIMIT_ENABLED（测试环境为 false，钩子直接
    return，本用例三条断言全红），或钩子调用被删。
    """
    monkeypatch.setattr(m.settings, "STARTUP_DB_HOOKS_ENABLED", True)
    rec_imports, rec_batches, rec_embed, rec_close = _arm(monkeypatch)

    with TestClient(app) as c:
        assert c.get("/health").status_code == 200  # 给事件循环一次驱动预热的机会
        assert _wait_until(lambda: rec_imports.calls), "导入恢复钩子未被调用"
        assert _wait_until(lambda: rec_batches.calls), "孤儿批次恢复钩子未被调用"
        assert _wait_until(lambda: rec_embed.calls), "embedding 预热未被调用"

    assert _wait_until(lambda: rec_close.calls), "lifespan 关闭段未真正调用 close_shared_client"


def test_startup_hooks_skipped_when_disabled(monkeypatch):
    """反向：开关=False（测试环境默认）→ 三件事一件都不做（不连 DB、不加载模型）。"""
    monkeypatch.setattr(m.settings, "STARTUP_DB_HOOKS_ENABLED", False)
    rec_imports, rec_batches, rec_embed, _rec_close = _arm(monkeypatch)

    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        time.sleep(0.05)  # 反向断言需要一段确定的观察窗口（钩子若误调，同步启动段必然已到）

    assert not rec_imports.calls, "开关关闭却执行了导入恢复钩子"
    assert not rec_batches.calls, "开关关闭却执行了孤儿批次恢复钩子"
    assert not rec_embed.calls, "开关关闭却加载了 embedding 模型"


def test_lifespan_shutdown_releases_every_resource(monkeypatch):
    """关停段行为守卫：`with TestClient(app)` 退出后，lifespan 关闭段该释放的五样东西全被真释放。

    被守住的失效形态：`app/main.py` 关停段任一步被注释/删除（stop_scheduler、两个线程池
    关停、两个共享 client 关闭）→ 对应断言立刻红。这是原 `test_lifespan_shutdown_calls_close`
    （inspect.getsource 找函数名的文本守卫）删掉后一直没补回来的那段覆盖。
    探针一律透传真实现，故不断言「桩被调到就完事」，也不给同 session 后续用例漏停线程/漏关池。
    """
    monkeypatch.setattr(m.settings, "STARTUP_DB_HOOKS_ENABLED", False)  # 启动侧由上面两条用例负责

    spies: dict[str, _Spy] = {}
    for mod, name in (
        (sched_mod, "stop_scheduler"),
        (ticket_agent_mod, "shutdown_draft_pool"),
        (shadow_mod, "shutdown_shadow_pool"),
    ):
        spies[name] = _Spy(getattr(mod, name))
        monkeypatch.setattr(mod, name, spies[name])
    for mod, name in (
        (chat_mod, "close_shared_client"),
        (vision_mod, "close_shared_vision_client"),
    ):
        spies[name] = _AsyncSpy(getattr(mod, name))
        monkeypatch.setattr(mod, name, spies[name])

    draft_pool_before = ticket_agent_mod._draft_pool
    shadow_pool_before = shadow_mod._shadow_pool

    with TestClient(app) as c:
        assert c.get("/health").status_code == 200

    for name in (
        "stop_scheduler",
        "shutdown_draft_pool",
        "shutdown_shadow_pool",
        "close_shared_client",
        "close_shared_vision_client",
    ):
        assert spies[name].calls, f"lifespan 关停段未调用 {name}"
    # 线程池的关停是真重建（旧实例 shutdown 后模块属性换新）——注释掉那一步 identity 不变
    assert ticket_agent_mod._draft_pool is not draft_pool_before, "草稿线程池未重建：shutdown_draft_pool 没生效"
    assert shadow_mod._shadow_pool is not shadow_pool_before, "影子线程池未重建：shutdown_shadow_pool 没生效"
