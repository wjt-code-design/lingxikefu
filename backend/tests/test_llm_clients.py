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
