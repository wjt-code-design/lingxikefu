"""Image Agent 测试：硬切限制 + 降级留痕（禁止静默降级）。"""
from __future__ import annotations

import pytest

from app.services.agents.image_agent import MAX_IMAGES, ImageAgent
from app.services.shared_context import SharedContext


@pytest.mark.asyncio
async def test_no_images_passthrough():
    ctx = SharedContext(query="保修多久")
    ctx = await ImageAgent().run(ctx)
    assert ctx.degraded == []
    assert ctx.fused_query == ""  # 无图不产生融合文本


@pytest.mark.asyncio
async def test_too_many_images_degrades_with_trace():
    paths = [f"ref{i}.jpg" for i in range(MAX_IMAGES + 1)]
    ctx = SharedContext(query="看看图", image_paths=paths)
    ctx = await ImageAgent().run(ctx)
    assert f"image:count>{MAX_IMAGES}" in ctx.degraded


@pytest.mark.asyncio
async def test_bad_extension_degrades_with_trace():
    ctx = SharedContext(query="看看图", image_paths=["file1.bmp"])
    ctx = await ImageAgent().run(ctx)
    assert any(d.startswith("image:bad_ext") for d in ctx.degraded)


@pytest.mark.asyncio
async def test_not_implemented_degrades_with_trace():
    """视觉通道未接入：合法图片也降级纯文字 + 留痕（问答流不中断）。"""
    ctx = SharedContext(query="看看图", image_paths=["file1.png", "file2.webp"])
    ctx = await ImageAgent().run(ctx)
    # 文件不存在会触发 vision_failed
    assert any(d.startswith("image:vision_failed") or d.startswith("image:all_failed") for d in ctx.degraded)
