"""LLM 客户端：chat / embedding / rerank 三统一入口（经 LiteLLM→百炼 + 本地 bge）。

- embedding：默认本地 bge-base-zh-v1.5（0 成本、不出境），可切百炼 text-embedding；
- chat：通义千问（默认 qwen3.7-flash），Key 走 env 注入；
- rerank：MVP 关闭，管线预留节点。
"""
from app.llm_clients.chat import BailianChatClient, get_chat_client
from app.llm_clients.embedding import (
    BailianEmbeddingClient,
    LocalEmbeddingClient,
    get_embedding_client,
)
from app.llm_clients.rerank import get_rerank_client

__all__ = [
    "BailianChatClient",
    "get_chat_client",
    "LocalEmbeddingClient",
    "BailianEmbeddingClient",
    "get_embedding_client",
    "get_rerank_client",
]
