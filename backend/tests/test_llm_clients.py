"""llm_clients 单测：配置路由 / 缺失 Key 报错 / 参数透传（全部 mock，不真调、不加载模型）。

注意：settings 是模块级 pydantic 单例，测试用 monkeypatch.setattr 直接改属性即可，
不需动环境变量（get_chat_client / get_embedding_client 有 lru_cache，测试间需 cache_clear）。

2026-08-27：全面取消百炼/智谱，仅 LongCat + 本地 bge。
"""
from __future__ import annotations

import json

import httpx
import pytest
from app.core.config import settings
from app.llm_clients.base import ModelNotConfiguredError
from app.llm_clients.chat import OpenAILikeChatClient, get_chat_client
from app.llm_clients.embedding import LocalEmbeddingClient, get_embedding_client
from app.llm_clients.rerank import get_rerank_client


class TestEmbeddingRouting:
    def test_local_default(self):
        assert settings.EMBEDDING_PROVIDER == "local"
        get_embedding_client.cache_clear()
        assert isinstance(get_embedding_client(), LocalEmbeddingClient)

    def test_non_local_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "bailian")
        get_embedding_client.cache_clear()
        with pytest.raises(ModelNotConfiguredError, match="EMBEDDING_PROVIDER"):
            get_embedding_client()


class TestLocalEmbedding:
    def test_embed_uses_normalized_vectors(self, monkeypatch):
        import numpy as np

        class FakeModel:
            def encode(self, texts, **kwargs):
                assert kwargs["normalize_embeddings"] is True
                assert kwargs["show_progress_bar"] is False
                return np.array([[0.1, 0.2] for _ in texts], dtype=float)

        monkeypatch.setattr(LocalEmbeddingClient, "_model", FakeModel())
        client = LocalEmbeddingClient()
        out = client.embed(["你好", "退货政策"])
        assert len(out) == 2
        assert all(len(v) == 2 and all(isinstance(x, float) for x in v) for v in out)

    def test_dim_constant(self):
        assert LocalEmbeddingClient.dim == 768


class TestLongCatChat:
    def test_no_key_raises_actionable_error(self, monkeypatch):
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", None)
        with pytest.raises(ModelNotConfiguredError, match="LONGCAT_API_KEY"):
            OpenAILikeChatClient()._api_key()

    @staticmethod
    def _fake_post(captured: dict):
        """替换 httpx.AsyncClient.post：不联网，捕获请求并返回固定响应。"""

        async def fake_post(self, url, content=None, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json.loads(content)

            class _R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": "你好，我是客服"}}]}

            return _R()

        return fake_post

    def test_complete_httpx(self, monkeypatch):
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-longcat-key")
        monkeypatch.setattr(settings, "LONGCAT_BASE_URL", "https://longcat.test/openai")
        monkeypatch.setattr(settings, "LONGCAT_CHAT_MODEL", "LongCat-2.0")
        captured: dict = {}
        monkeypatch.setattr(httpx.AsyncClient, "post", self._fake_post(captured))

        import asyncio

        client = OpenAILikeChatClient()
        out = asyncio.run(client.complete([{"role": "user", "content": "hi"}]))
        assert out == "你好，我是客服"
        assert captured["url"] == "https://longcat.test/openai/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer unit-longcat-key"
        assert captured["headers"]["Content-Type"] == "application/json"
        assert captured["body"]["model"] == "LongCat-2.0"
        assert captured["body"]["stream"] is False
        assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]

    def test_complete_disables_thinking_by_default(self, monkeypatch):
        """TTFT 优化：默认关闭 LongCat 思维链（enable_thinking=False），content 首字不等推理。"""
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-longcat-key")
        monkeypatch.setattr(settings, "LLM_ENABLE_THINKING", False)
        captured: dict = {}
        monkeypatch.setattr(httpx.AsyncClient, "post", self._fake_post(captured))

        import asyncio

        asyncio.run(OpenAILikeChatClient().complete([{"role": "user", "content": "hi"}]))
        assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}

    def test_complete_enable_thinking_respects_config(self, monkeypatch):
        """LLM_ENABLE_THINKING=true 时开回思维链（回归评测用）。"""
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-longcat-key")
        monkeypatch.setattr(settings, "LLM_ENABLE_THINKING", True)
        captured: dict = {}
        monkeypatch.setattr(httpx.AsyncClient, "post", self._fake_post(captured))

        import asyncio

        asyncio.run(OpenAILikeChatClient().complete([{"role": "user", "content": "hi"}]))
        assert "chat_template_kwargs" not in captured["body"]

    def test_payload_caller_kwargs_win(self, monkeypatch):
        """调用方显式传 chat_template_kwargs 时不被默认注入覆盖。"""
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-longcat-key")
        monkeypatch.setattr(settings, "LLM_ENABLE_THINKING", False)
        captured: dict = {}
        monkeypatch.setattr(httpx.AsyncClient, "post", self._fake_post(captured))

        import asyncio

        caller_kwargs = {"top_p": 0.5, "chat_template_kwargs": {"enable_thinking": True}}
        asyncio.run(OpenAILikeChatClient().complete([{"role": "user", "content": "hi"}], **caller_kwargs))
        assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": True}
        assert captured["body"]["top_p"] == 0.5

    @staticmethod
    def _fake_stream(captured: dict, sse_lines: list[str]):
        """替换 httpx.AsyncClient.stream：不联网，按行回放 SSE 流。"""

        def fake_stream(self, method, url, content=None, headers=None, timeout=None):
            captured["url"] = url
            captured["body"] = json.loads(content)

            class _R:
                def raise_for_status(self):
                    pass

                def aiter_lines(self):
                    async def gen():
                        for line in sse_lines:
                            yield line

                    return gen()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *exc):
                    return False

            return _R()

        return fake_stream

    def test_stream_events_yields_reasoning_and_content(self, monkeypatch):
        """思维链透传：reasoning_content 与 content 分型产出（感知 TTFT 基础）。"""
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-longcat-key")
        captured: dict = {}
        sse = [
            'data: {"choices":[{"delta":{"reasoning_content":"用户问退货"}}]}',
            'data: {"choices":[{"delta":{"reasoning_content":"资料有条款"}}]}',
            'data: {"choices":[{"delta":{"content":"退货政策如下"}}]}',
            "data: [DONE]",
        ]
        monkeypatch.setattr(httpx.AsyncClient, "stream", self._fake_stream(captured, sse))

        import asyncio

        async def collect():
            out = []
            async for kind, delta in OpenAILikeChatClient().stream_events([{"role": "user", "content": "q"}]):
                out.append((kind, delta))
            return out

        out = asyncio.run(collect())
        assert out == [
            ("reasoning", "用户问退货"),
            ("reasoning", "资料有条款"),
            ("content", "退货政策如下"),
        ]

    def test_stream_keeps_content_only_contract(self, monkeypatch):
        """旧 stream() 契约保持：只产出 content str（generate.py 等调用方不受影响）。"""
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-longcat-key")
        sse = [
            'data: {"choices":[{"delta":{"reasoning_content":"思考片段"}}]}',
            'data: {"choices":[{"delta":{"content":"正式回答"}}]}',
            "data: [DONE]",
        ]
        monkeypatch.setattr(
            httpx.AsyncClient, "stream", self._fake_stream({}, sse)
        )

        import asyncio

        async def collect():
            out = []
            async for delta in OpenAILikeChatClient().stream([{"role": "user", "content": "q"}]):
                out.append(delta)
            return out

        assert asyncio.run(collect()) == ["正式回答"]

    def test_stream_events_retries_on_429_then_succeeds(self, monkeypatch):
        """429 → sleep 2s 重试 1 次 → 成功产出（Batch3 覆盖率盲区：重试分支钉住）。

        raise_for_status 在响应头阶段抛出，两 attempt 均未 yield 任何 delta → 重试不产生
        重复内容（pitfall-sweep 结论的回归测试）。
        """
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-longcat-key")
        calls = {"n": 0}
        sse = ['data: {"choices":[{"delta":{"content":"重试后回答"}}]}', "data: [DONE]"]

        class _Resp:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                for line in sse:
                    yield line

        class _Ctx:
            async def __aenter__(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    req = httpx.Request("POST", "https://longcat.test/v1/chat/completions")
                    resp = httpx.Response(429, request=req)
                    raise httpx.HTTPStatusError("rate limited", request=req, response=resp)
                return _Resp()

            async def __aexit__(self, *exc):
                return False

        def fake_stream(self, method, url, content=None, headers=None, timeout=None):
            return _Ctx()

        monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)

        import asyncio

        async def collect():
            return [x async for x in OpenAILikeChatClient().stream_events([{"role": "user", "content": "q"}])]

        out = asyncio.run(collect())
        assert calls["n"] == 2
        assert out == [("content", "重试后回答")]


class TestSharedClientLifecycle:
    """共享 AsyncClient 生命周期（2026-09-02 pitfall-sweep）：关闭幂等 + 关后可重建。"""

    def test_close_shared_client_idempotent_and_rebuildable(self):
        import asyncio

        import app.llm_clients.chat as chat_mod

        async def run():
            c1 = await chat_mod._get_shared_client()
            assert c1 is not None and not c1.is_closed
            await chat_mod.close_shared_client()
            assert c1.is_closed
            # 幂等：再次关闭无异常
            await chat_mod.close_shared_client()
            # 关闭后 _get_shared_client 重建新实例
            c2 = await chat_mod._get_shared_client()
            assert c2 is not None and not c2.is_closed and c2 is not c1
            # 清理模块状态，防跨测试残留
            await chat_mod.close_shared_client()

        asyncio.run(run())
        assert chat_mod._shared_client is None

    def test_lifespan_shutdown_calls_close(self):
        """main.lifespan shutdown 段引用 close_shared_client（防被误删，删了必红）。"""
        import inspect

        import app.main as m

        src = inspect.getsource(m.lifespan)
        assert "close_shared_client" in src


class TestChatRouting:
    def test_routes_longcat(self, monkeypatch):
        monkeypatch.setattr(settings, "CHAT_PROVIDER", "longcat")
        get_chat_client.cache_clear()
        assert isinstance(get_chat_client(), OpenAILikeChatClient)

    def test_invalid_provider_routing(self, monkeypatch):
        monkeypatch.setattr(settings, "CHAT_PROVIDER", "bailian")
        get_chat_client.cache_clear()
        with pytest.raises(ModelNotConfiguredError, match="CHAT_PROVIDER"):
            get_chat_client()

        monkeypatch.setattr(settings, "CHAT_PROVIDER", "zhipu")
        get_chat_client.cache_clear()
        with pytest.raises(ModelNotConfiguredError, match="CHAT_PROVIDER"):
            get_chat_client()


class TestRerankGate:
    def test_disabled_by_default_raises(self):
        assert settings.RAG_ENABLE_RERANK is False
        with pytest.raises(ModelNotConfiguredError):
            get_rerank_client()


class TestOwnClientCrossLoop:
    """worker 线程 asyncio.run 跨 event loop 复用共享 AsyncClient 的修复（2026-09-03 审计）。

    背景：httpx AsyncClient 连接池绑定创建时的 loop；intent_shadow / ticket 预起草在
    worker 线程用 asyncio.run 每任务新 loop，复用共享 client → 概率性
    RuntimeError: Event loop is closed（离线回填 362 条 6 轮才收敛的根因；在线影子
    采样存活率低 + ticket 预起草静默 NULL 同源）。
    修复：complete(own_client=True) 走短命自建 client（多付一次握手，占后台预算 <10%）。

    用真实本地 HTTP/1.1 keep-alive 服务复现（mock post 绕过连接池无法复现此 bug）。
    """

    @staticmethod
    def _local_llm_server():
        """起一个返回固定 chat completion 的本地 HTTP/1.1 keep-alive 服务。

        线程化 + handler 读超时的原因（挂起教训）：HTTPServer.shutdown() 会等
        serve_forever 退出，而 keep-alive handler 阻塞在「等死连接的下一个请求」
        的 recv 上 → shutdown 无限挂起（首个测试版本的实际挂点）。ThreadingHTTPServer
        每连接独立线程 + timeout=5 让 handler 自行超时退出，finally 只关监听
        socket（server_close 非阻塞）。
        """
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            # HTTP/1.1 + Content-Length → keep-alive 连接复用（复现连接池行为的前提）
            protocol_version = "HTTP/1.1"
            # keep-alive 等下一请求 5s 超时退出（防 handler 永久阻塞）
            timeout = 5

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.dumps(
                    {"choices": [{"message": {"content": "ok"}}]}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def _setup(self, monkeypatch):
        import app.llm_clients.chat as chat_mod

        server = self._local_llm_server()
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-key")
        monkeypatch.setattr(
            settings, "LONGCAT_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}/v1"
        )
        # 隔离共享池状态：置空模块级单例（跨 loop 的旧连接不泄漏到其他测试）
        chat_mod._shared_client = None
        return server, chat_mod

    @staticmethod
    def _run(coro, seconds: float = 8.0):
        """asyncio.run + 硬超时：跨 loop 死连接的失败形态是**挂死**（RuntimeError 在
        httpcore 内部回调被吞，探针实证 req2 无限等待）而非干净异常——不加 wait_for
        测试会永远挂起（与回填 6 轮收敛中「挂到 10s 超时」的表现一致）。"""
        import asyncio

        return asyncio.run(asyncio.wait_for(coro, seconds))

    def test_shared_client_cross_loop_fails_documented(self, monkeypatch):
        """文档化测试（bug 复现证据，2026-09-03 审计）：共享 client 的 keep-alive
        连接绑定创建时的 loop；第二个 loop 复用时 is_closed 守卫失明（仍 False）→
        checkout 死 loop 连接。实际失败形态**非确定**（探针实证：RuntimeError 或
        挂死二选一，即回填 6 轮收敛 ~50% 失败率的来源）——线程 + join 超时两种
        形态都判「bug 复现成功」；若未来 httpx 自愈（请求成功返回），本测试失败
        = 可连同 own_client 分支一并简化。"""
        import asyncio
        import threading

        server, chat_mod = self._setup(monkeypatch)
        try:
            cli = chat_mod.OpenAILikeChatClient()
            # loop 1：一次请求即让 keep-alive 连接驻留共享池
            self._run(cli.complete([{"role": "user", "content": "q1"}], timeout=5))
            c1 = chat_mod._shared_client
            # 根因结构断言：连接所属 loop 已死，但 is_closed 守卫不感知
            assert c1 is not None and not c1.is_closed
            # loop 2：死连接上的真实请求——err（RuntimeError/超时）或挂死均为复现
            result: dict = {}

            def _doomed() -> None:
                try:
                    result["out"] = asyncio.run(
                        cli.complete([{"role": "user", "content": "q2"}], timeout=3)
                    )
                except BaseException as e:  # noqa: BLE001 - 记录形态供断言
                    result["err"] = f"{type(e).__name__}: {e}"

            t = threading.Thread(target=_doomed, daemon=True)
            t.start()
            t.join(15)
            if not t.is_alive():
                assert "err" in result, f"共享路径意外自愈（可移除 own_client 分支）：{result}"
                assert "Event loop is closed" in result["err"] or "Timeout" in result["err"], result
            # t.is_alive() → 挂死形态：daemon 线程随进程退出，同为 bug 复现
        finally:
            chat_mod._shared_client = None
            server.server_close()

    def test_complete_own_client_survives_cross_loop(self, monkeypatch):
        """修复验证：own_client=True 不触碰共享池（自建短命 client）。
        红测形态：修复前 own_client kwarg 被吞进 payload → 走共享池 → 命中
        _no_shared 断言（快且确定）；修复后自建 client 成功返回。
        跨 loop 死连接的实际危害由 test_shared_client_cross_loop_fails_documented
        单独锁定，本测试不需要真实跨 loop 预热。"""
        server, chat_mod = self._setup(monkeypatch)
        try:
            cli = chat_mod.OpenAILikeChatClient()

            async def _no_shared():
                raise AssertionError("own_client 路径不得触碰共享池")

            monkeypatch.setattr(chat_mod, "_get_shared_client", _no_shared)
            # 仍在独立 loop 上调用（复现 worker asyncio.run 场景）
            out = self._run(
                cli.complete([{"role": "user", "content": "q2"}], timeout=5, own_client=True)
            )
            assert out == "ok"
        finally:
            chat_mod._shared_client = None
            server.server_close()

    def test_complete_own_client_default_unchanged(self, monkeypatch):
        """默认路径（主 loop 请求）行为零变化：仍走共享池（TTFT 红利保持）。"""
        server, chat_mod = self._setup(monkeypatch)
        try:
            cli = chat_mod.OpenAILikeChatClient()
            out = self._run(cli.complete([{"role": "user", "content": "q"}], timeout=5))
            assert out == "ok"
            assert chat_mod._shared_client is not None  # 共享池被使用且保留
        finally:
            chat_mod._shared_client = None
            server.server_close()
