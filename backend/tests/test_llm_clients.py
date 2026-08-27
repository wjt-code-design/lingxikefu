"""llm_clients 单测：配置路由 / 缺失 Key 报错 / 参数透传（全部 mock，不真调、不加载模型）。

注意：settings 是模块级 pydantic 单例，测试用 monkeypatch.setattr 直接改属性即可，
不需动环境变量（get_chat_client / get_embedding_client 有 lru_cache，测试间需 cache_clear）。
"""
from __future__ import annotations

import json

import httpx
import pytest
from app.core.config import settings
from app.llm_clients.base import ModelNotConfiguredError
from app.llm_clients.chat import (
    FallbackChatClient,
    OpenAILikeChatClient,
    get_chat_client,
)
from app.llm_clients.embedding import (
    BailianEmbeddingClient,
    LocalEmbeddingClient,
    get_embedding_client,
)
from app.llm_clients.rerank import get_rerank_client


class TestEmbeddingRouting:
    def test_local_default(self):
        assert settings.EMBEDDING_PROVIDER == "local"
        get_embedding_client.cache_clear()
        assert isinstance(get_embedding_client(), LocalEmbeddingClient)

    def test_bailian_route(self, monkeypatch):
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "bailian")
        get_embedding_client.cache_clear()
        assert isinstance(get_embedding_client(), BailianEmbeddingClient)

    def test_invalid_provider(self, monkeypatch):
        monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "unknown")
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


class TestOpenAILikeChat:
    def test_invalid_provider_fails_closed(self):
        with pytest.raises(ModelNotConfiguredError, match="provider"):
            OpenAILikeChatClient("unknown")

    def test_bailian_no_key_raises_actionable_error(self, monkeypatch):
        monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", None)
        with pytest.raises(ModelNotConfiguredError, match="DASHSCOPE_API_KEY"):
            OpenAILikeChatClient("bailian")._api_key()

    def test_zhipu_no_key_raises_actionable_error(self, monkeypatch):
        monkeypatch.setattr(settings, "ZHIPU_API_KEY", None)
        with pytest.raises(ModelNotConfiguredError, match="ZHIPU_API_KEY"):
            OpenAILikeChatClient("zhipu")._api_key()

    @staticmethod
    def _fake_post(captured: dict):
        """替换 httpx.AsyncClient.post：不联网，捕获请求并返回固定响应。"""

        async def fake_post(self, url, content=None, headers=None):
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

    def test_bailian_complete_httpx(self, monkeypatch):
        monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "unit-test-key")
        monkeypatch.setattr(settings, "CHAT_MODEL", "qwen3.7-flash-2026-07-15")
        monkeypatch.setattr(settings, "DASHSCOPE_BASE_URL", "https://example.test/v1")
        captured: dict = {}
        monkeypatch.setattr(httpx.AsyncClient, "post", self._fake_post(captured))

        import asyncio

        client = OpenAILikeChatClient("bailian")
        out = asyncio.run(client.complete([{"role": "user", "content": "hi"}]))
        assert out == "你好，我是客服"
        assert captured["url"] == "https://example.test/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer unit-test-key"
        assert captured["headers"]["Content-Type"] == "application/json"
        assert captured["body"]["model"] == "qwen3.7-flash-2026-07-15"
        assert captured["body"]["stream"] is False
        assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]

    def test_zhipu_complete_httpx(self, monkeypatch):
        monkeypatch.setattr(settings, "ZHIPU_API_KEY", "unit-zhipu-key")
        monkeypatch.setattr(settings, "ZHIPU_BASE_URL", "https://zhipu.test/v4")
        monkeypatch.setattr(settings, "ZHIPU_CHAT_MODEL", "glm-5.1")
        captured: dict = {}
        monkeypatch.setattr(httpx.AsyncClient, "post", self._fake_post(captured))

        import asyncio

        client = OpenAILikeChatClient("zhipu")
        out = asyncio.run(client.complete([{"role": "user", "content": "hi"}]))
        assert out == "你好，我是客服"
        assert captured["url"] == "https://zhipu.test/v4/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer unit-zhipu-key"
        assert captured["body"]["model"] == "glm-5.1"

    def test_longcat_complete_httpx(self, monkeypatch):
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", "unit-longcat-key")
        monkeypatch.setattr(settings, "LONGCAT_BASE_URL", "https://longcat.test/openai")
        monkeypatch.setattr(settings, "LONGCAT_CHAT_MODEL", "LongCat-2.0")
        captured: dict = {}
        monkeypatch.setattr(httpx.AsyncClient, "post", self._fake_post(captured))

        import asyncio

        client = OpenAILikeChatClient("longcat")
        out = asyncio.run(client.complete([{"role": "user", "content": "hi"}]))
        assert out == "你好，我是客服"
        assert captured["url"] == "https://longcat.test/openai/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer unit-longcat-key"
        assert captured["body"]["model"] == "LongCat-2.0"

    def test_longcat_no_key_raises_actionable_error(self, monkeypatch):
        monkeypatch.setattr(settings, "LONGCAT_API_KEY", None)
        with pytest.raises(ModelNotConfiguredError, match="LONGCAT_API_KEY"):
            OpenAILikeChatClient("longcat")._api_key()


class TestChatRouting:
    def test_routes_by_provider(self, monkeypatch):
        monkeypatch.setattr(settings, "CHAT_PROVIDER", "zhipu")
        get_chat_client.cache_clear()
        assert isinstance(get_chat_client(), OpenAILikeChatClient)
        assert get_chat_client().provider == "zhipu"

        monkeypatch.setattr(settings, "CHAT_PROVIDER", "longcat")
        get_chat_client.cache_clear()
        assert isinstance(get_chat_client(), OpenAILikeChatClient)
        assert get_chat_client().provider == "longcat"

        monkeypatch.setattr(settings, "CHAT_PROVIDER", "bailian")
        get_chat_client.cache_clear()
        # 百炼包 FallbackChatClient（额度 403 自动降级智谱），主 provider 仍为 bailian
        fb = get_chat_client()
        assert isinstance(fb, FallbackChatClient)
        assert fb.primary.provider == "bailian"
        assert fb.fallback.provider == "zhipu"

    def test_invalid_provider_routing(self, monkeypatch):
        monkeypatch.setattr(settings, "CHAT_PROVIDER", "unknown")
        get_chat_client.cache_clear()
        with pytest.raises(ModelNotConfiguredError, match="CHAT_PROVIDER"):
            get_chat_client()


class TestFallbackChat:
    """百炼额度 403 → 自动降级智谱；非额度错误不降级（fail-closed）。"""

    @staticmethod
    def _setup_env(monkeypatch):
        monkeypatch.setattr(settings, "CHAT_PROVIDER", "bailian")
        monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "unit-bailian-key")
        monkeypatch.setattr(settings, "DASHSCOPE_BASE_URL", "https://bailian.test/v1")
        monkeypatch.setattr(settings, "CHAT_MODEL", "qwen3.7-flash-2026-07-15")
        monkeypatch.setattr(settings, "ZHIPU_API_KEY", "unit-zhipu-key")
        monkeypatch.setattr(settings, "ZHIPU_BASE_URL", "https://zhipu.test/v4")
        monkeypatch.setattr(settings, "ZHIPU_CHAT_MODEL", "glm-5.1")
        get_chat_client.cache_clear()

    def test_complete_403_falls_back_to_zhipu(self, monkeypatch):
        self._setup_env(monkeypatch)
        captured: dict = {}

        async def fake_post(self, url, content=None, headers=None):
            captured["url"] = url
            if "bailian.test" in url:
                req = httpx.Request("POST", url)
                return httpx.Response(403, request=req)  # 百炼免费额度耗尽
            class _R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"choices": [{"message": {"content": "来自智谱"}}]}

            return _R()

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        import asyncio

        out = asyncio.run(get_chat_client().complete([{"role": "user", "content": "hi"}]))
        assert out == "来自智谱"
        assert captured["url"].startswith("https://zhipu.test")

    def test_complete_non_quota_error_not_fallback(self, monkeypatch):
        self._setup_env(monkeypatch)

        async def fake_post(self, url, content=None, headers=None):
            req = httpx.Request("POST", url)
            return httpx.Response(404, request=req)  # 404 非额度错误，不应降级

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        import asyncio

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(get_chat_client().complete([{"role": "user", "content": "hi"}]))

    def test_stream_403_falls_back_to_zhipu(self, monkeypatch):
        self._setup_env(monkeypatch)
        seen_urls: list[str] = []

        class FakeResp:
            def __init__(self, url):
                self._url = url

            def raise_for_status(self):
                if "bailian.test" in self._url:
                    req = httpx.Request("POST", self._url)
                    raise httpx.HTTPStatusError(
                        "403 Forbidden",
                        request=req,
                        response=httpx.Response(403, request=req),
                    )

            async def aiter_lines(self):
                if "bailian.test" in self._url:
                    return
                yield 'data: {"choices":[{"delta":{"content":"流式"}}]}'
                yield "data: [DONE]"

        class FakeStreamCM:
            def __init__(self, url):
                self._url = url

            async def __aenter__(self):
                return FakeResp(self._url)

            async def __aexit__(self, *a):
                return False

        class FakeAsyncClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def stream(self, method, url, content=None, headers=None):
                seen_urls.append(url)
                return FakeStreamCM(url)

        def client_factory(*a, **k):
            return FakeAsyncClient()

        monkeypatch.setattr(httpx, "AsyncClient", client_factory)
        import asyncio

        client = get_chat_client()

        async def collect(messages):
            out = []
            async for d in client.stream(messages):
                out.append(d)
            return out

        deltas = asyncio.run(collect([{"role": "user", "content": "hi"}]))
        assert deltas == ["流式"]
        assert seen_urls[0].startswith("https://bailian.test")
        assert seen_urls[-1].startswith("https://zhipu.test")


class TestRerankGate:
    def test_disabled_by_default_raises(self):
        assert settings.RAG_ENABLE_RERANK is False
        with pytest.raises(ModelNotConfiguredError, match="RAG_ENABLE_RERANK"):
            get_rerank_client()
