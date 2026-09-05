"""FAQ 响应模型（Phase 4）：帮助中心匿名公开访问的知识库文档清单（仅名称级信息，无 chunk 全文）。"""
from __future__ import annotations

from pydantic import BaseModel


class FaqDocItem(BaseModel):
    doc_id: str
    name: str
    status: str
    chunks: int


class FaqDocContentResp(BaseModel):
    """方案A：政策原文浏览——单篇已索引文档的公开原文（raw_text）。"""

    doc_id: str
    name: str
    kb_name: str
    status: str
    content: str


class FaqKbItem(BaseModel):
    kb_id: str
    kb_name: str
    description: str | None = None
    doc_count: int
    chunk_count: int
    docs: list[FaqDocItem] = []


class FaqListResp(BaseModel):
    items: list[FaqKbItem]
