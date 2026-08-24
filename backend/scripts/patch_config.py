"""Patch config.py to add VOLCENGINE_* fields after ZHIPU_CHAT_MODEL line."""
import re
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "app" / "core" / "config.py"
content = p.read_text(encoding="utf-8")

target = '    ZHIPU_CHAT_MODEL: str = "glm-5.1"\n'
insertion = (
    '    # 火山引擎（视觉模型）：Image Agent 图片理解\n'
    '    VOLCENGINE_API_KEY: str | None = None\n'
    '    VOLCENGINE_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"\n'
    '    VOLCENGINE_CHAT_MODEL: str = "Doubao-Seedance-1.0-pro-fast"\n'
)

if target not in content:
    raise SystemExit("target line not found in config.py")

new_content = content.replace(target, target + insertion)
p.write_text(new_content, encoding="utf-8")
print("config.py patched successfully")
