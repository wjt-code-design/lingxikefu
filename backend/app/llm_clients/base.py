"""LLM 客户端抽象基类与异常。

chat 走 httpx 直连 OpenAI 兼容端点（唯一 provider LongCat，见 chat.py）；
embedding 本地 bge（独立实现）。无 Key / 未启用时抛 ``ModelNotConfiguredError``，
报错信息必须可操作（告诉用户配什么）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ModelNotConfiguredError(RuntimeError):
    """模型未配置 / 未启用。消息需指明缺失的环境变量与模型名。"""


class EmbeddingClient(ABC):
    """向量化接口：输入文本列表，输出 float 向量列表。"""

    dim: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。实现方负责批量限流 / 重试。"""


class ChatClient(ABC):
    """对话接口：流式（BU-06 SSE 用）与非流式（评测用）。"""

    @abstractmethod
    async def stream(self, messages: list[dict], **kwargs):
        """流式生成，yield (delta: str) 增量；错误抛 ModelNotConfiguredError / 上层重试。"""

    @abstractmethod
    async def complete(self, messages: list[dict], **kwargs) -> str:
        """一次性生成完整回答。"""
