"""Chat 客户端：经 LiteLLM 调百炼（OpenAI 兼容端点）。

chat 主模型默认 qwen3.7-flash（快 + 便宜）；fallback 模型在调用方决定重试时传入。
Key 从 ``DASHSCOPE_API_KEY`` 读取（env 注入），缺失时抛可操作的错误。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

from app.core.config import settings
from app.llm_clients.base import ChatClient, ModelNotConfiguredError


class BailianChatClient(ChatClient):
    """通义千问（经 LiteLLM→百炼兼容端点），流式 / 非流式。"""

    def _api_key(self) -> str:
        if not settings.DASHSCOPE_API_KEY:
            raise ModelNotConfiguredError(
                "chat 需要配置 DASHSCOPE_API_KEY（后端 .env 的 DASHSCOPE_API_KEY= 一行），当前为空"
            )
        return settings.DASHSCOPE_API_KEY

    def _model_name(self, model: str | None) -> str:
        # LiteLLM 对 OpenAI 兼容端点：前缀 openai/ 指向 api_base
        return f"openai/{model or settings.CHAT_MODEL}"

    async def stream(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        import litellm

        key = self._api_key()
        resp = await litellm.acompletion(
            model=self._model_name(model),
            messages=messages,
            api_base=settings.DASHSCOPE_BASE_URL,
            api_key=key,
            stream=True,
            **kwargs,
        )
        async for chunk in resp:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    async def complete(self, messages: list[dict], model: str | None = None, **kwargs) -> str:
        import litellm

        key = self._api_key()
        resp = await litellm.acompletion(
            model=self._model_name(model),
            messages=messages,
            api_base=settings.DASHSCOPE_BASE_URL,
            api_key=key,
            stream=False,
            **kwargs,
        )
        return resp.choices[0].message.content or ""


@lru_cache(maxsize=1)
def get_chat_client() -> ChatClient:
    return BailianChatClient()
