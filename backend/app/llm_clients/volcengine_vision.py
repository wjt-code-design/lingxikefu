"""火山引擎视觉客户端：用于 Image Agent 图片理解。

使用 Doubao-Seedance-1.0-pro-fast 模型，支持图片+文字输入，输出图片描述。
API 兼容 OpenAI 格式，但需要特殊的消息结构（content 为数组）。
"""
from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path

import httpx
from fastapi.concurrency import run_in_threadpool

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# B2-9：模块级共享 AsyncClient（对齐 chat.py _shared_client 模式）——旧实现每次
# describe_image 新建 client，每图多付一次 TCP+TLS 握手。ImageAgent 恒在主事件循环
# await 调用（无 worker 线程 asyncio.run 路径），共享池无跨 loop 风险。
# 测试通过 monkeypatch httpx.AsyncClient.post 类方法 mock，对单例实例同样生效。
_shared_vision_client: httpx.AsyncClient | None = None
_vision_client_lock = asyncio.Lock()


async def _get_shared_vision_client() -> httpx.AsyncClient:
    global _shared_vision_client
    if _shared_vision_client is None or _shared_vision_client.is_closed:
        async with _vision_client_lock:
            if _shared_vision_client is None or _shared_vision_client.is_closed:
                _shared_vision_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _shared_vision_client


async def close_shared_vision_client() -> None:
    """关闭共享视觉 client（lifespan shutdown 调用）：释放 keep-alive 连接池。幂等。"""
    global _shared_vision_client
    async with _vision_client_lock:
        if _shared_vision_client is not None and not _shared_vision_client.is_closed:
            await _shared_vision_client.aclose()
        _shared_vision_client = None


def _load_image_b64(image_path: Path) -> tuple[str, str]:
    """同步读盘 + base64 + MIME 推断（B2-9：搬 worker 线程，≤10MB 读盘不阻塞事件循环）。

    文件不存在抛 FileNotFoundError（传播到调用方，语义不变）。
    """
    if not image_path.exists():
        raise FileNotFoundError(f"图片文件不存在: {image_path}")
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    mime_type = _MIME_MAP.get(image_path.suffix.lower(), "image/jpeg")
    return image_b64, mime_type


class VolcengineVisionClient:
    """火山引擎视觉客户端。"""

    def __init__(self) -> None:
        if not settings.VOLCENGINE_API_KEY:
            raise RuntimeError("VOLCENGINE_API_KEY 未配置")
        self.api_key = settings.VOLCENGINE_API_KEY
        self.base_url = settings.VOLCENGINE_BASE_URL.rstrip("/")
        self.model = settings.VOLCENGINE_CHAT_MODEL

    async def describe_image(self, image_path: str | Path, text_query: str = "") -> str:
        """描述图片内容。

        Args:
            image_path: 图片文件路径
            text_query: 可选的文字查询（如用户的问题）

        Returns:
            图片描述文本
        """
        image_path = Path(image_path)
        # B2-9：读盘 + base64 + MIME 推断搬 worker 线程（≤10MB 读盘不阻塞事件循环）
        image_b64, mime_type = await run_in_threadpool(_load_image_b64, image_path)

        # 构建消息内容（火山引擎视觉 API 格式）
        content_parts = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{image_b64}"
                }
            }
        ]

        # 如果有文字查询，添加到消息中
        if text_query:
            content_parts.append({
                "type": "text",
                "text": text_query
            })
        else:
            content_parts.append({
                "type": "text",
                "text": "请详细描述这张图片的内容。"
            })

        messages = [
            {
                "role": "user",
                "content": content_parts
            }
        ]

        # 调用 API
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024
        }

        # B2-9：共享 AsyncClient（keep-alive 复用，不再每图重建连接池）
        client = await _get_shared_vision_client()
        try:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            # 提取响应文本
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("API 返回空响应")

            message = choices[0].get("message", {})
            content = message.get("content", "")
            return content.strip()

        except httpx.HTTPStatusError as e:
            logger.error(f"火山引擎 API 调用失败: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"火山引擎视觉客户端异常: {e}")
            raise


# 单例工厂
_vision_client: VolcengineVisionClient | None = None


def get_vision_client() -> VolcengineVisionClient:
    """获取火山引擎视觉客户端单例。"""
    global _vision_client
    if _vision_client is None:
        _vision_client = VolcengineVisionClient()
    return _vision_client
