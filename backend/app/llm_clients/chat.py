"""Chat 客户端：OpenAI 兼容端点直连（百炼 / 智谱双 provider，httpx 实现）。

- ``CHAT_PROVIDER=bailian``（默认）：通义千问 qwen3.7-flash，Key 读 DASHSCOPE_API_KEY；
- ``CHAT_PROVIDER=zhipu``：智谱 GLM（glm-4.7），Key 读 ZHIPU_API_KEY（百炼额度耗尽时的备选）。
- 用 httpx 直连（不用 litellm）：litellm 在 Windows 对中文消息序列化有 ascii codec bug；
  httpx 显式 ``content=json.dumps(..., ensure_ascii=False).encode('utf-8')`` 保证 UTF-8。
- Key 缺失时抛可操作错误（不静默）。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from functools import lru_cache

import httpx

from app.core.config import settings
from app.llm_clients.base import ChatClient, ModelNotConfiguredError

#: 流式默认 60s（边收边发足够）；非流式 complete 用 120s（推理模型长回答 + 慢网络兜底）
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_COMPLETE_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
#: 429/5xx 重试 1 次（间隔 2s）：智谱/百炼 TPM 限流是分钟级窗口，偶发 429 重试可自愈
_RETRY_STATUS = {429, 500, 502, 503}


class OpenAILikeChatClient(ChatClient):
    """OpenAI 兼容端点客户端（流式/非流式），provider 参数化。"""

    def __init__(self, provider: str) -> None:
        # provider 必须显式传入（配置单一真源，禁止代码内第二份默认值）；
        # 非法值 fail-closed，避免 else 分支把未知 provider 静默当 bailian。
        if provider not in ("bailian", "zhipu"):
            raise ModelNotConfiguredError(
                f"chat provider 非法值: {provider!r}（可选: bailian / zhipu，由 CHAT_PROVIDER 配置）"
            )
        self.provider = provider

    # --- provider 配置 ---
    def _api_key(self) -> str:
        if self.provider == "zhipu":
            key = settings.ZHIPU_API_KEY
            err = "chat(provider=zhipu) 需要配置 ZHIPU_API_KEY（后端 .env 的 ZHIPU_API_KEY= 一行）"
        else:
            key = settings.DASHSCOPE_API_KEY
            err = "chat(provider=bailian) 需要配置 DASHSCOPE_API_KEY（后端 .env 的 DASHSCOPE_API_KEY= 一行）"
        if not key:
            raise ModelNotConfiguredError(err + "，当前为空")
        return key

    def _api_url(self) -> str:
        base = settings.ZHIPU_BASE_URL if self.provider == "zhipu" else settings.DASHSCOPE_BASE_URL
        return base.rstrip("/") + "/chat/completions"

    def _default_model(self) -> str:
        return settings.ZHIPU_CHAT_MODEL if self.provider == "zhipu" else settings.CHAT_MODEL

    def _request(self, payload: dict) -> tuple[dict, bytes]:
        """统一请求头与 UTF-8 请求体（stream/complete 共用）。

        content=bytes 已是 UTF-8，不加 charset 头：httpx 对带 charset 的
        Content-Type 值按 charset 编码字符串，Windows 下中文会走 ascii 报错（实测踩坑）。
        """
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        # ensure_ascii=False + 显式 UTF-8 字节：杜绝中文被按 ascii/GBK 编码
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return headers, body

    async def stream(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        payload = {
            "model": model or self._default_model(),
            "messages": messages,
            "stream": True,
            **{k: v for k, v in kwargs.items() if k not in ("stream",)},
        }
        headers, body = self._request(payload)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            last_err: httpx.HTTPStatusError | None = None
            for _attempt in range(2):  # 429/5xx 重试 1 次（TPM 分钟窗口偶发，2s 后自愈）
                try:
                    async with client.stream("POST", self._api_url(), content=body, headers=headers) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = chunk.get("choices") or []
                            if choices:
                                delta = (choices[0].get("delta") or {}).get("content")
                                if delta:
                                    yield delta
                        return
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response.status_code in _RETRY_STATUS:
                        await asyncio.sleep(2)
                        continue
                    raise
            assert last_err is not None
            raise last_err

    async def complete(self, messages: list[dict], model: str | None = None, **kwargs) -> str:
        payload = {
            "model": model or self._default_model(),
            "messages": messages,
            "stream": False,
            **{k: v for k, v in kwargs.items() if k not in ("stream",)},
        }
        headers, body = self._request(payload)
        async with httpx.AsyncClient(timeout=_COMPLETE_TIMEOUT) as client:
            last_err: httpx.HTTPStatusError | None = None
            for _attempt in range(2):  # 429/5xx 重试 1 次（非流式长回答 + 偶发限流双兜底）
                try:
                    resp = await client.post(self._api_url(), content=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        return ""
                    return (choices[0].get("message") or {}).get("content") or ""
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response.status_code in _RETRY_STATUS:
                        await asyncio.sleep(2)
                        continue
                    raise
            assert last_err is not None
            raise last_err


@lru_cache(maxsize=1)
def get_chat_client() -> ChatClient:
    provider = settings.CHAT_PROVIDER.lower()
    if provider not in ("bailian", "zhipu"):
        raise ModelNotConfiguredError(f"CHAT_PROVIDER 非法值: {provider!r}（可选: bailian / zhipu）")
    return OpenAILikeChatClient(provider)
