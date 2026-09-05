"""Chat 客户端：OpenAI 兼容端点直连（LongCat，httpx 实现）。

- ``CHAT_PROVIDER=longcat``：LongCat LongCat-2.0，Key 读 LONGCAT_API_KEY。
- 用 httpx 直连（不用 litellm）：litellm 在 Windows 对中文消息序列化有 ascii codec bug；
  httpx 显式 ``content=json.dumps(..., ensure_ascii=False).encode('utf-8')`` 保证 UTF-8。
- Key 缺失时抛可操作错误（不静默）。
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from functools import lru_cache

import httpx

from app.core.config import settings
from app.llm_clients.base import ChatClient, ModelNotConfiguredError

logger = logging.getLogger(__name__)

#: 流式默认 60s（边收边发足够）；非流式 complete 用 120s（推理模型长回答 + 慢网络兜底）
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_COMPLETE_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
#: 429/5xx 重试 1 次（间隔 2s）：限流是分钟级窗口，偶发 429 重试可自愈
_RETRY_STATUS = {429, 500, 502, 503}

# ---- 共享 AsyncClient（TTFT 优化） ----
# 每请求 async with httpx.AsyncClient() 会重建连接池 → 每问必付 TCP+TLS 握手（0.3~1s）。
# 模块级单例复用 keep-alive 连接；timeout 按请求覆盖（stream 60s / complete 120s 不同）。
# 测试通过 monkeypatch httpx.AsyncClient.post 类方法 mock，对单例实例同样生效。
_shared_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        async with _client_lock:
            if _shared_client is None or _shared_client.is_closed:
                _shared_client = httpx.AsyncClient(trust_env=True)
    return _shared_client


async def close_shared_client() -> None:
    """关闭共享 AsyncClient（lifespan shutdown 调用）：释放 keep-alive 连接池。

    幂等：未创建/已关闭均为无害空操作；下次请求经 _get_shared_client 重建。
    """
    global _shared_client
    async with _client_lock:
        if _shared_client is not None and not _shared_client.is_closed:
            await _shared_client.aclose()
        _shared_client = None


class OpenAILikeChatClient(ChatClient):
    """OpenAI 兼容端点客户端（流式/非流式），固定 LongCat provider。"""

    def _api_key(self) -> str:
        if not settings.LONGCAT_API_KEY:
            raise ModelNotConfiguredError(
                "chat(provider=longcat) 需要配置 LONGCAT_API_KEY（后端 .env 的 LONGCAT_API_KEY= 一行），当前为空"
            )
        return settings.LONGCAT_API_KEY

    def _api_url(self) -> str:
        return settings.LONGCAT_BASE_URL.rstrip("/") + "/chat/completions"

    def _default_model(self) -> str:
        return settings.LONGCAT_CHAT_MODEL

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

    def _payload(self, messages: list[dict], model: str | None, stream: bool, kwargs: dict) -> dict:
        """统一 payload 组装：模型/流式开关 + 思维链开关（TTFT 优化）。

        LongCat-2.0 是推理模型：默认输出思维链（reasoning_content），content 首字要等
        思维链完成（实测 TTFT 4~19s）。LLM_ENABLE_THINKING=False（默认）时注入
        enable_thinking=False 关闭思维链；调用方显式传 chat_template_kwargs 时不覆盖。
        """
        payload = {
            "model": model or self._default_model(),
            "messages": messages,
            "stream": stream,
            **{k: v for k, v in kwargs.items() if k not in ("stream",)},
        }
        if stream:
            # usage 埋点（2026-09-05 LongCat 收费）：流式默认不返回 usage，显式要求
            # 末块附带（实测 LongCat 支持，usage 块 choices 为空、不进正文）。
            payload.setdefault("stream_options", {"include_usage": True})
        if not settings.LLM_ENABLE_THINKING and "chat_template_kwargs" not in payload:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    @staticmethod
    def _log_usage(usage: dict | None, *, path: str, model: str) -> None:
        """结构化 usage 日志（成本对账唯一数据源；聚合口径=按日 sum 各字段）。

        fail-open：字段缺失只打已知部分，绝不为观测反噬主链路（无 usage 直接跳过）。
        字段对齐 LongCat 计费维度：cached 输入 ¥0.04/M vs 未命中 ¥2/M（折扣价），
        reasoning 计入 completion 价——分开记才能算出真实钱账。
        """
        if not usage:
            return
        try:
            details_c = usage.get("completion_tokens_details") or {}
            details_p = usage.get("prompt_tokens_details") or {}
            logger.info(
                "llm_usage path=%s model=%s prompt_tokens=%s completion_tokens=%s "
                "reasoning_tokens=%s cached_tokens=%s",
                path,
                model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                details_c.get("reasoning_tokens", 0),
                details_p.get("cached_tokens", 0),
            )
        except Exception:  # noqa: BLE001 - 观测代码永不影响主链路
            logger.debug("llm_usage 解析失败（忽略）", exc_info=True)

    async def stream_events(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> AsyncGenerator[tuple[str, str], None]:
        """流式产出 (kind, delta)：kind ∈ {"reasoning", "content"}。

        思维链透传（TTFT 感知优化）：LongCat 开思考时 reasoning_content 先于 content
        流式到达（实测首块 ~2s），上层可把"思考中"即时反馈给用户；content 为正式回答。
        """
        payload = self._payload(messages, model, stream=True, kwargs=kwargs)
        headers, body = self._request(payload)
        client = await _get_shared_client()
        last_err: httpx.HTTPStatusError | None = None
        for _attempt in range(2):  # 429/5xx 重试 1 次（TPM 分钟窗口偶发，2s 后自愈）
            try:
                usage: dict | None = None
                async with client.stream("POST", self._api_url(), content=body, headers=headers, timeout=_TIMEOUT) as resp:
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
                        # include_usage 末块：usage 携带整次请求 token 账（choices 为空）
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            reasoning = delta.get("reasoning_content")
                            if reasoning:
                                yield ("reasoning", reasoning)
                            content = delta.get("content")
                            if content:
                                yield ("content", content)
                self._log_usage(usage, path="stream", model=str(payload.get("model") or ""))
                return
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code in _RETRY_STATUS:
                    await asyncio.sleep(2)
                    continue
                raise
        assert last_err is not None
        raise last_err

    async def stream(
        self, messages: list[dict], model: str | None = None, **kwargs
    ) -> AsyncGenerator[str, None]:
        """旧契约：仅 yield content delta（str）。思维链场景用 stream_events()。"""
        async for kind, delta in self.stream_events(messages, model, **kwargs):
            if kind == "content":
                yield delta

    async def complete(self, messages: list[dict], model: str | None = None, **kwargs) -> str:
        # P2-⑤：允许调用方显式覆写超时（如坐席辅助用短超时 25s，低于前端阈值 35s）
        req_timeout = kwargs.pop("timeout", None)
        # 2026-09-03：worker 线程 asyncio.run 每任务新 loop，复用共享池会 checkout 绑定
        # 死 loop 的 keep-alive 连接（概率性 Event loop is closed / 挂死——离线回填
        # 362 条 6 轮才收敛的根因，在线影子采样存活率低 + ticket 预起草静默 NULL 同源）。
        # own_client=True 走短命自建 client：多付一次 TCP+TLS 握手（占后台预算 <10%），
        # 主 loop 流量不受影响（默认仍走共享池，TTFT 红利保持）。
        own_client = kwargs.pop("own_client", False)
        payload = self._payload(messages, model, stream=False, kwargs=kwargs)
        headers, body = self._request(payload)
        client_timeout = (
            _COMPLETE_TIMEOUT
            if req_timeout is None
            else httpx.Timeout(float(req_timeout), connect=10.0)
        )
        last_err: httpx.HTTPStatusError | None = None
        for _attempt in range(2):  # 429/5xx 重试 1 次（非流式长回答 + 偶发限流双兜底）
            try:
                if own_client:
                    async with httpx.AsyncClient(trust_env=True, timeout=client_timeout) as client:
                        resp = await client.post(self._api_url(), content=body, headers=headers)
                else:
                    client = await _get_shared_client()
                    resp = await client.post(self._api_url(), content=body, headers=headers, timeout=client_timeout)
                resp.raise_for_status()
                data = resp.json()
                self._log_usage(data.get("usage"), path="complete", model=str(payload.get("model") or ""))
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
    # 平台已收敛：仅 longcat 合法（2026-08-27 全面取消百炼/智谱）；fail-closed 防配置漂移
    if provider != "longcat":
        raise ModelNotConfiguredError(
            f"CHAT_PROVIDER 非法值: {provider!r}（当前仅支持 longcat）"
        )
    return OpenAILikeChatClient()
