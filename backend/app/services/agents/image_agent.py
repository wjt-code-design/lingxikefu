"""Image Agent：图片理解 + 文字融合。

使用火山引擎 Doubao-Seedance-1.0-pro-fast 模型进行图片理解。
支持：
- 图片描述生成
- 图片+文字融合查询
- 硬切限制（大小/格式/数量）
- 失败降级留痕
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.llm_clients.volcengine_vision import get_vision_client
from app.services.agents.base import BaseAgent
from app.services.shared_context import SharedContext

logger = logging.getLogger(__name__)

# 硬切限制
MAX_IMAGES = 3  # 最多处理 3 张图片
MAX_IMAGE_SIZE_MB = 10  # 单张图片最大 10MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class ImageAgent(BaseAgent):
    """图片理解 Agent：调用火山引擎视觉模型生成图片描述。"""

    name = "image_agent"

    async def run(self, ctx: SharedContext) -> SharedContext:
        """执行图片理解。

        Args:
            ctx: 共享上下文，包含 image_paths 和 query

        Returns:
            更新后的上下文，包含 image_descriptions 和 fused_query
        """
        if not ctx.image_paths:
            return ctx

        # 硬切检查
        if len(ctx.image_paths) > MAX_IMAGES:
            ctx.degraded.append(f"image:count>{MAX_IMAGES}")
            logger.warning(f"图片数量超限: {len(ctx.image_paths)} > {MAX_IMAGES}")
            return ctx

        try:
            vision_client = get_vision_client()
            descriptions = []

            for image_path_str in ctx.image_paths:
                image_path = Path(image_path_str)

                # 格式检查
                if image_path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    ctx.degraded.append(f"image:bad_ext:{image_path.suffix}")
                    logger.warning(f"不支持的图片格式: {image_path}")
                    continue

                # 大小检查
                if image_path.exists():
                    size_mb = image_path.stat().st_size / (1024 * 1024)
                    if size_mb > MAX_IMAGE_SIZE_MB:
                        ctx.degraded.append(f"image:too_large:{size_mb:.1f}MB")
                        logger.warning(f"图片过大: {image_path} ({size_mb:.1f}MB)")
                        continue

                # 调用视觉模型
                try:
                    description = await vision_client.describe_image(
                        image_path,
                        text_query=ctx.query
                    )
                    descriptions.append(description)
                    logger.info(f"图片描述生成成功: {image_path.name}")
                except Exception as e:
                    ctx.degraded.append(f"image:vision_failed:{image_path.name}")
                    logger.error(f"视觉模型调用失败: {image_path} - {e}")
                    continue

            # 融合查询
            if descriptions:
                ctx.image_descriptions = descriptions
                # 构建融合查询：用户问题 + 图片描述
                desc_text = "；".join(descriptions)
                ctx.fused_query = f"{ctx.query}（图片内容：{desc_text}）"
                logger.info(f"融合查询生成: {ctx.fused_query[:100]}...")
            else:
                # 所有图片都失败，降级
                ctx.degraded.append("image:all_failed")
                logger.warning("所有图片处理失败，降级为纯文本查询")

        except Exception as e:
            ctx.degraded.append(f"image:agent_error:{type(e).__name__}")
            logger.error(f"ImageAgent 执行异常: {e}", exc_info=True)

        return ctx
