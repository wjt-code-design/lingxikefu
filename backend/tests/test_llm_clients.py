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
