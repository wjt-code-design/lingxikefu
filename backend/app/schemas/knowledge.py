"""知识库 / 文档请求响应模型（BU-04），与 contracts/api.ts Knowledge 段对齐。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocStatus = Literal["parsing", "embedding", "indexed", "failed"]


class CreateKBReq(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class KBItem(BaseModel):
    kb_id: str
    name: str
    doc_count: int
    chunk_count: int


class KBListResp(BaseModel):
    items: list[KBItem]


class DocItem(BaseModel):
    doc_id: str
    name: str
    status: DocStatus
    chunks: int
    error: str | None = None


class DocumentListResp(BaseModel):
    items: list[DocItem]


class OkResp(BaseModel):
    ok: bool = True
