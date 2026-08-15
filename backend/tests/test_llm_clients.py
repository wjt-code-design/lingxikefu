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
from app.llm_clients.chat import OpenAILikeChatClient, get_chat_client
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


class TestChatRouting:
    def test_routes_by_provider(self, monkeypatch):
        monkeypatch.setattr(settings, "CHAT_PROVIDER", "zhipu")
        get_chat_client.cache_clear()
        assert isinstance(get_chat_client(), OpenAILikeChatClient)
        assert get_chat_client().provider == "zhipu"

        monkeypatch.setattr(settings, "CHAT_PROVIDER", "bailian")
        get_chat_client.cache_clear()
        assert get_chat_client().provider == "bailian"

    def test_invalid_provider_routing(self, monkeypatch):
        monkeypatch.setattr(settings, "CHAT_PROVIDER", "unknown")
        get_chat_client.cache_clear()
        with pytest.raises(ModelNotConfiguredError, match="CHAT_PROVIDER"):
            get_chat_client()


class TestRerankGate:
    def test_disabled_by_default_raises(self):
        assert settings.RAG_ENABLE_RERANK is False
        with pytest.raises(ModelNotConfiguredError, match="RAG_ENABLE_RERANK"):
            get_rerank_client()
