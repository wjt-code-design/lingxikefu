"""冒烟测试：验证百炼三端点连通性（chat / 视觉 / rerank）。

用途：换模型/换端点后第一时间验证链路是否通（比跑全量评测快 100 倍）。
放在 experiments/（实验与诊断脚本区），**不进生产 scripts/**。

用法（backend 目录下）：
    .venv/Scripts/python.exe experiments/smoke_bailian.py

退出码：0=三端点全通；1=有端点失败。
注意：本脚本会**真实调用外部 API**（消耗少量额度），仅在配置变更后跑。
"""
from __future__ import annotations

import sys
from pathlib import Path

# experiments/ 的上一级是 backend/，加进 sys.path 以便 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402


def _mask(s: str) -> str:
    return f"{s[:12]}...{s[-6:]}" if len(s) > 24 else "***"


def test_chat() -> bool:
    """chat/completions —— OpenAI 兼容端点。"""
    import httpx

    url = settings.LONGCAT_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.LONGCAT_CHAT_MODEL,
        "messages": [{"role": "user", "content": "用一句话回答：1+1等于几？"}],
        "stream": False,
        "max_tokens": 200,
        "enable_thinking": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.LONGCAT_API_KEY}",
        "Content-Type": "application/json",
    }
    print(f"[chat] POST {url}")
    print(f"[chat] model={settings.LONGCAT_CHAT_MODEL} key={_mask(settings.LONGCAT_API_KEY or '')}")
    try:
        with httpx.Client(timeout=60.0) as c:
            r = c.post(url, json=payload, headers=headers)
        print(f"[chat] HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"[chat] BODY {r.text[:600]}")
            return False
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage") or {}
        print(f"[chat] OK content={content[:120]!r}")
        print(f"[chat] usage={usage}")
        return bool(content)
    except Exception as e:  # noqa: BLE001
        print(f"[chat] FAIL {type(e).__name__}: {e}")
        return False


def _make_test_png_b64(size: int = 64) -> str:
    """生成一张 size×size 的纯红 PNG（base64）。

    ⚠️ 不能用 1x1：qwen3.8 视觉模型要求宽高 > 10px，1x1 会被拒
    （HTTP 400 "image length and width do not meet the model restrictions"）。
    """
    import base64

    try:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (size, size), (255, 0, 0)).save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        # 无 Pillow：手工构造 PNG（zlib 编码全红扫描线）
        import struct
        import zlib

        w = h = size
        raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))

        def _chunk(tag: bytes, data: bytes) -> bytes:
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

        png = (
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b"")
        )
        return base64.b64encode(png).decode()


def test_vision() -> bool:
    """视觉模型 —— OpenAI 兼容端点（content 数组 + image_url data URI）。"""
    import httpx

    png_b64 = _make_test_png_b64(64)
    url = settings.VOLCENGINE_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.VOLCENGINE_CHAT_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{png_b64}"},
                    },
                    {"type": "text", "text": "这张图是什么颜色？"},
                ],
            }
        ],
        "stream": False,
        "max_tokens": 100,
        "enable_thinking": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.VOLCENGINE_API_KEY}",
        "Content-Type": "application/json",
    }
    print(f"[vision] POST {url} model={settings.VOLCENGINE_CHAT_MODEL}")
    try:
        with httpx.Client(timeout=60.0) as c:
            r = c.post(url, json=payload, headers=headers)
        print(f"[vision] HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"[vision] BODY {r.text[:600]}")
            return False
        data = r.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        print(f"[vision] OK content={content[:200]!r}")
        # 语义正确性：纯红图 → 模型应答"红"
        return bool(content) and "红" in content
    except Exception as e:  # noqa: BLE001
        print(f"[vision] FAIL {type(e).__name__}: {e}")
        return False


def test_rerank() -> bool:
    """rerank —— 百炼原生端点（不走 compatible-mode）。"""
    from app.llm_clients.rerank_bailian import rerank

    docs = [
        "员工每年享有 5 天带薪年假，入职满一年后开始计算。",
        "量子计算是计算科学的一个前沿领域。",
        "公司为全体员工提供补充医疗保险，覆盖门诊与住院费用。",
    ]
    print(f"[rerank] model={settings.RERANK_MODEL}")
    print(f"[rerank] base={settings.RERANK_BASE_URL}")
    try:
        results = rerank("年假有多少天", docs, top_n=2)
        print(f"[rerank] OK {results}")
        ok = len(results) >= 1 and results[0].index == 0
        print(f"[rerank] top1 应为年假那条(idx=0)，实际 idx={results[0].index if results else None}")
        return ok
    except Exception as e:  # noqa: BLE001
        print(f"[rerank] FAIL {type(e).__name__}: {e}")
        return False


def test_rerank_wrong_path() -> None:
    """反证：确认 compatible-mode/v1/rerank 确实是 404 空 body（记录坑）。"""
    import httpx

    url = settings.LONGCAT_BASE_URL.rstrip("/") + "/rerank"
    headers = {
        "Authorization": f"Bearer {settings.LONGCAT_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=20.0) as c:
            r = c.post(url, json={"model": "x", "input": {}}, headers=headers)
        print(f"[坑验证] compatible-mode/v1/rerank → HTTP {r.status_code}, body长度={len(r.text)}")
    except Exception as e:  # noqa: BLE001
        print(f"[坑验证] 异常 {type(e).__name__}: {e}")


if __name__ == "__main__":
    print("=== 百炼三端点冒烟 ===")
    results = {
        "chat": test_chat(),
        "vision": test_vision(),
        "rerank": test_rerank(),
    }
    test_rerank_wrong_path()
    print("\n=== 结果 ===")
    for k, v in results.items():
        print(f"  {k}: {'PASS' if v else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)
