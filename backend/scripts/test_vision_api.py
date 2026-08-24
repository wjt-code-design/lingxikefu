"""火山引擎视觉 API 端到端测试。

验证 Image Agent 能否成功调用火山引擎 Doubao-Seedance-1.0-pro-fast 模型。
"""
import asyncio
import sys
from pathlib import Path

# 添加 backend 到 Python 路径
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.services.agents.image_agent import ImageAgent
from app.services.shared_context import SharedContext


async def test_vision_api():
    """测试火山引擎视觉 API 调用。"""
    print("=" * 60)
    print("火山引擎视觉 API 端到端测试")
    print("=" * 60)

    # B1：ImageAgent 仅允许读取上传白名单目录内的图片——把白名单指向 backend 目录
    settings.IMAGE_UPLOAD_DIR = str(backend_dir)

    # 创建一个测试图片路径（使用项目中可能存在的图片）
    test_images = [
        str(backend_dir / "test_image.jpg"),
        str(backend_dir / "test_image.png"),
    ]

    # 创建 SharedContext
    ctx = SharedContext(
        query="这张图片展示了什么？",
        image_paths=test_images
    )

    print("\n测试参数:")
    print(f"  - 查询: {ctx.query}")
    print(f"  - 图片路径: {ctx.image_paths}")

    # 执行 Image Agent
    agent = ImageAgent()
    print("\n执行 Image Agent...")

    try:
        result = await agent.run(ctx)

        print("\n执行结果:")
        print(f"  - 融合查询: {result.fused_query}")
        print(f"  - 图片描述数量: {len(result.image_descriptions)}")
        print(f"  - 降级标记: {result.degraded}")

        if result.image_descriptions:
            print("\n图片描述内容:")
            for i, desc in enumerate(result.image_descriptions, 1):
                print(f"  [{i}] {desc[:200]}...")

        if result.degraded:
            print("\n⚠️  发生降级，原因:")
            for d in result.degraded:
                print(f"  - {d}")
        else:
            print("\n✅ 视觉 API 调用成功！")

        return len(result.degraded) == 0

    except Exception as e:
        print(f"\n❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_vision_api())
    sys.exit(0 if success else 1)
