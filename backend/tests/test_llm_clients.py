"""llm_clients 单测：配置路由 / 缺失 Key 报错 / 参数透传（全部 mock，不真调、不加载模型）。

注意：settings 是模块级 pydantic 单例，测试用 monkeypatch.setattr 直接改属性即可，
不需动环境变量（get_embedding_client 有 lru_cache，测试间需 cache_clear）。
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.llm_clients.base import ModelNotConfiguredError
from app.llm_clients.chat import BailianChatClient, get_chat_client
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


class TestBailianChat:
    def test_no_key_raises_actionable_error(self, monkeypatch):
        monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", None)
        with pytest.raises(ModelNotConfiguredError, match="DASHSCOPE_API_KEY"):
            BailianChatClient()._api_key()

    def test_complete_passes_params(self, monkeypatch):
        monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "unit-test-key")
        monkeypatch.setattr(settings, "CHAT_MODEL", "qwen3.7-flash-2026-07-15")
        monkeypatch.setattr(settings, "DASHSCOPE_BASE_URL", "https://example.test/v1")

        class FakeMsg:
            content = "你好，我是客服"

        class FakeChoice:
            message = FakeMsg()

        class FakeResp:
            choices = [FakeChoice()]

        async def fake_acompletion(**kwargs):
            captured["model"] = kwargs["model"]
            captured["api_base"] = kwargs["api_base"]
            captured["api_key"] = kwargs["api_key"]
            captured["stream"] = kwargs["stream"]
            return FakeResp()

        captured: dict = {}
        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        import asyncio

        client = BailianChatClient()
        out = asyncio.run(
            client.complete([{"role": "user", "content": "hi"}])
        )
        assert out == "你好，我是客服"
        assert captured["model"] == "openai/qwen3.7-flash-2026-07-15"
        assert captured["api_base"] == "https://example.test/v1"
        assert captured["api_key"] == "unit-test-key"
        assert captured["stream"] is False


class TestRerankGate:
    def test_disabled_by_default_raises(self):
        assert settings.RAG_ENABLE_RERANK is False
        with pytest.raises(ModelNotConfiguredError, match="RAG_ENABLE_RERANK"):
            get_rerank_client()
