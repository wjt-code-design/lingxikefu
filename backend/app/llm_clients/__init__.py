"""LLM 客户端：chat / embedding / rerank 统一入口（LongCat + 本地 bge）。

- embedding：本地 bge-base-zh-v1.5（0 成本、不出境，2026-08-27 取消百炼 embedding）；
- chat：OpenAI 兼容单 provider——LongCat LongCat-2.0，Key 走 env 注入；
- rerank：MVP 关闭，管线预留节点。
"""
from app.llm_clients.chat import OpenAILikeChatClient, get_chat_client
from app.llm_clients.embedding import LocalEmbeddingClient, get_embedding_client
from app.llm_clients.rerank import get_rerank_client

__all__ = [
    "OpenAILikeChatClient",
    "get_chat_client",
    "LocalEmbeddingClient",
    "get_embedding_client",
    "get_rerank_client",
]
