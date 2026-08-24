"""火山引擎视觉客户端：用于 Image Agent 图片理解。

使用 Doubao-Seedance-1.0-pro-fast 模型，支持图片+文字输入，输出图片描述。
API 兼容 OpenAI 格式，但需要特殊的消息结构（content 为数组）。
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


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
        if not image_path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        # 读取图片并转为 base64
        image_bytes = image_path.read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        # 推断 MIME 类型
        suffix = image_path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        mime_type = mime_map.get(suffix, "image/jpeg")

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

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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
