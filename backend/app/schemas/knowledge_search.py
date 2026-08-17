"""知识检索请求 / 响应模型（Phase 4）：agent 客服工作台检索知识库。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgeSearchReq(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    kb_id: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)


class KnowledgeSearchHit(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    kb_id: str
    kb_name: str
    snippet: str
    score: float
    dense_score: float


class KnowledgeSearchResp(BaseModel):
    query: str
    hits: list[KnowledgeSearchHit]
