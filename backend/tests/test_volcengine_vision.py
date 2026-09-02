"""volcengine_vision 客户端单测（Batch 覆盖率盲区 43%→100%）。

不联网：monkeypatch httpx.AsyncClient.post；图片用 tmp_path 假文件（base64 编码真实执行）。
覆盖：构造校验 / MIME 映射与回退 / 正常路径（payload 结构 + content strip）/
空响应 / HTTPStatusError 与通用异常分支 / 单例工厂。
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import httpx
import pytest
from app.core.config import settings
from app.llm_clients.volcengine_vision import (
    VolcengineVisionClient,
    get_vision_client,
)


def make_png(tmp_path: Path, name: str = "img.png") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x89PNG-fake-bytes")
    return p


def make_client(monkeypatch: pytest.MonkeyPatch) -> VolcengineVisionClient:
    monkeypatch.setattr(settings, "VOLCENGINE_API_KEY", "unit-volc-key")
    return VolcengineVisionClient()


def fake_post(monkeypatch: pytest.MonkeyPatch, captured: dict, payload_out: dict, status: int = 200):
    """替换 httpx.AsyncClient.post：捕获请求体，回放给定响应。"""

    async def fake_post_impl(self, url, *, json=None, headers=None, **kw):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        resp = httpx.Response(status, json=payload_out, request=httpx.Request("POST", url))
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post_impl)


# ---------- 构造 ----------

def test_init_requires_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "VOLCENGINE_API_KEY", None)
    with pytest.raises(RuntimeError, match="VOLCENGINE_API_KEY"):
        VolcengineVisionClient()


def test_init_uses_settings_and_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "VOLCENGINE_API_KEY", "unit-volc-key")
    monkeypatch.setattr(settings, "VOLCENGINE_BASE_URL", "https://ark.test/api/v3/")
    c = VolcengineVisionClient()
    assert c.base_url == "https://ark.test/api/v3"  # 尾部 / 被剥（拼接 url 不会双斜杠）
    assert c.model == settings.VOLCENGINE_CHAT_MODEL


# ---------- describe_image ----------

def test_describe_image_file_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    c = make_client(monkeypatch)
    with pytest.raises(FileNotFoundError):
        asyncio.run(c.describe_image(tmp_path / "missing.png"))


def test_describe_image_success_payload_and_strip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    c = make_client(monkeypatch)
    img = make_png(tmp_path)
    captured: dict = {}
    out_text = "  图中是一台洗衣机。  "
    fake_post(monkeypatch, captured, {"choices": [{"message": {"content": out_text}}]})

    got = asyncio.run(c.describe_image(img, text_query="这是什么家电？"))
    assert got == "图中是一台洗衣机。"  # strip
    # 请求结构：URL / 认证头 / payload 关键字段
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer unit-volc-key"
    body = captured["json"]
    assert body["model"] == settings.VOLCENGINE_CHAT_MODEL
    assert body["temperature"] == 0.7 and body["max_tokens"] == 1024
    content = body["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    expect_b64 = base64.b64encode(img.read_bytes()).decode()
    assert content[0]["image_url"]["url"] == f"data:image/png;base64,{expect_b64}"
    # 有文字查询 → 用户问题作为 text part
    assert content[1] == {"type": "text", "text": "这是什么家电？"}


def test_describe_image_default_text_without_query(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    c = make_client(monkeypatch)
    captured: dict = {}
    fake_post(monkeypatch, captured, {"choices": [{"message": {"content": "描述"}}]})
    asyncio.run(c.describe_image(make_png(tmp_path)))
    content = captured["json"]["messages"][0]["content"]
    assert content[1]["text"] == "请详细描述这张图片的内容。"


@pytest.mark.parametrize(
    ("name", "expect_mime"),
    [
        ("img.jpg", "image/jpeg"),
        ("img.jpeg", "image/jpeg"),
        ("img.webp", "image/webp"),
        ("img.bmp", "image/jpeg"),  # 未知后缀回退
    ],
)
def test_describe_image_mime_mapping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, expect_mime: str
):
    c = make_client(monkeypatch)
    captured: dict = {}
    fake_post(monkeypatch, captured, {"choices": [{"message": {"content": "ok"}}]})
    asyncio.run(c.describe_image(make_png(tmp_path, name)))
    url = captured["json"]["messages"][0]["content"][0]["image_url"]["url"]
    assert url.startswith(f"data:{expect_mime};base64,")


def test_describe_image_empty_choices_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    c = make_client(monkeypatch)
    fake_post(monkeypatch, {}, {"choices": []})
    with pytest.raises(RuntimeError, match="空响应"):
        asyncio.run(c.describe_image(make_png(tmp_path)))


def test_describe_image_http_error_logged_and_reraised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
):
    c = make_client(monkeypatch)
    fake_post(monkeypatch, {}, {"detail": "boom"}, status=500)
    with caplog.at_level("ERROR", logger="app.llm_clients.volcengine_vision"):
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(c.describe_image(make_png(tmp_path)))
    assert any("火山引擎 API 调用失败" in r.getMessage() for r in caplog.records)


def test_describe_image_unexpected_error_logged_and_reraised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog
):
    c = make_client(monkeypatch)

    async def broken_post(self, url, **kw):
        raise ValueError("json decode disaster")

    monkeypatch.setattr(httpx.AsyncClient, "post", broken_post)
    with caplog.at_level("ERROR", logger="app.llm_clients.volcengine_vision"):
        with pytest.raises(ValueError):
            asyncio.run(c.describe_image(make_png(tmp_path)))
    assert any("火山引擎视觉客户端异常" in r.getMessage() for r in caplog.records)


# ---------- 单例工厂 ----------

def test_get_vision_client_singleton(monkeypatch: pytest.MonkeyPatch):
    import app.llm_clients.volcengine_vision as mod

    monkeypatch.setattr(mod, "_vision_client", None)
    monkeypatch.setattr(settings, "VOLCENGINE_API_KEY", "unit-volc-key")
    c1 = get_vision_client()
    c2 = get_vision_client()
    assert c1 is c2
    monkeypatch.setattr(mod, "_vision_client", None)  # 清理防跨测试残留
