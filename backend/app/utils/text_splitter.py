"""文本分块工具（BU-04）：按段落聚合 + 定长窗口切分，标题行保留在块首。

策略（简单可靠，避免过度工程）：
1. 按空行把文本切成自然段（保留段落内换行结构）；
2. 顺序合并相邻段，使每块尽量接近 ``chunk_size``（不超上限）；
3. 单段超长则按 ``chunk_size`` 硬切，overlap 保留上一块尾部（跨块上下文）；
4. 返回非空块列表，每块 strip。

参数边界：``chunk_size > 0``，``0 <= chunk_overlap < chunk_size``，违反即抛 ``ValueError``
（配置非法应在启动/导入时 fail-closed，而非静默切出坏块）。
"""
from __future__ import annotations

import re


def split_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if not (0 <= chunk_overlap < chunk_size):
        raise ValueError("must satisfy 0 <= chunk_overlap < chunk_size")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        if len(para) > chunk_size:
            # 单段超长：先落盘当前 buffer，再对段落硬切
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_hard_split(para, chunk_size, chunk_overlap))
            continue
        # 合并后不超上限则追加；否则落盘重开，并带上上一块尾部作 overlap
        if buffer and len(buffer) + 1 + len(para) > chunk_size:
            chunks.append(buffer)
            tail = chunks[-1][-chunk_overlap:] if chunk_overlap and chunks else ""
            buffer = (tail + "\n" if tail else "") + para
        else:
            buffer = f"{buffer}\n{para}" if buffer else para
    if buffer:
        chunks.append(buffer)
    return [c.strip() for c in chunks if c.strip()]


def _hard_split(text: str, size: int, overlap: int) -> list[str]:
    """超长段按字符窗口硬切，overlap 处重入以保留上下文。"""
    out: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + size, n)
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= n:
            break
        start = max(end - overlap, 0)
    return out


# ---------- snippet 清洗（UI 审查 2026-08-31 高2） ----------
# chunk 保留 markdown 标记（见 split_text：标题行保留在块首），直接硬截 200 字符会把
# `## 五、发货提醒` 等源码和句中半截话透给前端来源面板。展示层统一经 clean_snippet。

_MD_HEADER_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.MULTILINE)  # 行首标题标记（保留标题文字）
_MD_BULLET_RE = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)  # 行首列表标记（保留列表文字）
_MD_INLINE_RE = re.compile(r"\*{1,3}|_{2,3}|`{1,3}")  # 行内强调/代码标记（单 _ 保留，防误伤标识符）
_MD_TABLE_RE = re.compile(r"^[ \t]*\|", re.MULTILINE)  # 表格行首竖线
_NEWLINE_RE = re.compile(r"[ \t]*\n+[ \t]*")  # 换行归一为单个空格
_SENTENCE_END = "。！？；!?)）\"”』」"


def clean_snippet(text: str, limit: int = 200) -> str:
    """清洗 chunk 原文为展示用摘录：剥 markdown 标记 + 优先句界截断。

    - 超长时截到最后一个句末标点（在 limit 内），避免从句中截断；
    - 无任何句末标点则硬切到 limit（不补省略号，保持长度契约 <= limit）；
    - 纯展示用途，不参与检索/缓存键。
    """
    if not text:
        return ""
    out = _MD_HEADER_RE.sub("", text)
    out = _MD_BULLET_RE.sub("", out)
    out = _MD_INLINE_RE.sub("", out)
    out = _MD_TABLE_RE.sub("", out)
    out = _NEWLINE_RE.sub(" ", out).strip()
    if len(out) > limit:
        cut = -1
        for i in range(limit):
            if out[i] in _SENTENCE_END:
                cut = i
        out = out[: cut + 1] if cut >= 0 else out[:limit]
    return out
