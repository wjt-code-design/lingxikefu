"""Image Agent 测试：硬切限制 + 路径白名单（B1）+ 降级留痕（禁止静默降级）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from app.core.config import settings
from app.services.agents.image_agent import MAX_IMAGES, ImageAgent
from app.services.shared_context import SharedContext


@pytest.fixture
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """B1：把上传白名单目录指到临时目录，测试路径全部落白名单内。"""
    monkeypatch.setattr(settings, "IMAGE_UPLOAD_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_no_images_passthrough():
    ctx = SharedContext(query="保修多久")
    ctx = await ImageAgent().run(ctx)
    assert ctx.degraded == []
    assert ctx.fused_query == ""  # 无图不产生融合文本


@pytest.mark.asyncio
async def test_too_many_images_degrades_with_trace(upload_dir: Path):
    paths = [str(upload_dir / f"ref{i}.jpg") for i in range(MAX_IMAGES + 1)]
    ctx = SharedContext(query="看看图", image_paths=paths)
    ctx = await ImageAgent().run(ctx)
    assert f"image:count>{MAX_IMAGES}" in ctx.degraded


@pytest.mark.asyncio
async def test_bad_extension_degrades_with_trace(upload_dir: Path):
    ctx = SharedContext(query="看看图", image_paths=[str(upload_dir / "file1.bmp")])
    ctx = await ImageAgent().run(ctx)
    assert any(d.startswith("image:bad_ext") for d in ctx.degraded)


@pytest.mark.asyncio
async def test_vision_failed_degrades_with_trace(upload_dir: Path):
    """白名单内但不存在的文件：走到视觉调用失败 → 降级留痕（问答流不中断）。"""
    ctx = SharedContext(
        query="看看图",
        image_paths=[str(upload_dir / "file1.png"), str(upload_dir / "file2.webp")],
    )
    ctx = await ImageAgent().run(ctx)
    # 文件不存在会触发 vision_failed
    assert any(d.startswith("image:vision_failed") or d.startswith("image:all_failed") for d in ctx.degraded)


@pytest.mark.asyncio
async def test_path_outside_whitelist_rejected(upload_dir: Path, tmp_path_factory):
    """B1：白名单外路径（绝对路径 / ../ 逃逸）一律拒绝读取并留痕。"""
    outside = tmp_path_factory.mktemp("outside") / "secret.png"
    ctx = SharedContext(
        query="看看图",
        image_paths=[
            "/etc/passwd",                              # 任意绝对路径
            str(outside),                               # 白名单外目录
            str(upload_dir / ".." / ".." / "escape.png"),  # ../ 逃逸
        ],
    )
    ctx = await ImageAgent().run(ctx)
    # 3 条全部被拒 → path_forbidden 留痕 3 次，且不触发视觉调用
    assert ctx.degraded.count("image:path_forbidden") == 3
    assert ctx.image_descriptions == []
    assert ctx.fused_query == ""
