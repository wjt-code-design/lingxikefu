"""Embedding 双后端：本地 bge（默认）/ 百炼 text-embedding（可切换）。

选型决策（2026-08-15）：
- 默认 ``local``：BAAI/bge-base-zh-v1.5（本机 HF 缓存 781M 完整），0 成本、数据不出境；
- 切 ``bailian`` 需重建全部向量索引（维度不同：bge=768 / text-embedding-v3=1024），
  由 M2 评测召回不达标时再做，代码仅需改 ``EMBEDDING_PROVIDER`` 环境变量。
- 注意：bge 中文检索需给 query 加指令前缀（官方推荐），document 不加。
"""
from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache

from app.core.config import settings
from app.llm_clients.base import EmbeddingClient, ModelNotConfiguredError

logger = logging.getLogger(__name__)

# bge-base-zh-v1.5 官方推荐的 query 指令前缀（提升检索质量，document 不加）
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

#: Qdrant 集合维度：不同 provider 必须一致，切换 provider 需重建集合
LOCAL_BGE_DIM = 768
BAILIAN_EMBEDDING_DIM = 1024


class LocalEmbeddingClient(EmbeddingClient):
    """本机 sentence-transformers bge 模型，懒加载 + 进程内单例。"""

    dim = LOCAL_BGE_DIM
    _model = None
    _lock = threading.Lock()

    def _ensure_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        # 本地模型强制离线加载：模型已在本机缓存，联网检查/下载反而
                        # 在企业网络触发 SSL 校验失败；离线也符合"数据不出境"原则。
                        os.environ.setdefault("HF_HUB_OFFLINE", "1")
                        from sentence_transformers import SentenceTransformer
                    except ImportError as e:  # pragma: no cover - 环境依赖
                        raise ModelNotConfiguredError(
                            "本地 embedding 需要 sentence-transformers，请安装："
                            f"pip install sentence-transformers（缺失原始错误: {e}）"
                        ) from e
                    logger.info("加载本地 embedding 模型 %s ...", settings.EMBEDDING_MODEL)
                    self._model = SentenceTransformer(settings.EMBEDDING_MODEL)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        # 中文 bge：query 加指令前缀；此处为通用接口，前缀由检索侧决定是否携带，
        # 实现统一不加，检索侧对 query 单独加（见 sparse/vector_service 后续接入）。
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]


class BailianEmbeddingClient(EmbeddingClient):
    """百炼 text-embedding，经 LiteLLM OpenAI 兼容端点调用。"""

    dim = BAILIAN_EMBEDDING_DIM

    def _api_key(self) -> str:
        if not settings.DASHSCOPE_API_KEY:
            raise ModelNotConfiguredError(
                "EMBEDDING_PROVIDER=bailian 需要配置 DASHSCOPE_API_KEY（后端 .env），当前为空"
            )
        return settings.DASHSCOPE_API_KEY

    def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm

        key = self._api_key()
        resp = litellm.embedding(
            model=f"openai/{settings.EMBEDDING_MODEL}",
            input=texts,
            api_base=settings.DASHSCOPE_BASE_URL,
            api_key=key,
        )
        # 按输入顺序取向量（百炼兼容端点返回与输入同序）
        return [item["embedding"] for item in resp.data]


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    """按 EMBEDDING_PROVIDER 路由（local|bailian），进程内单例。"""
    provider = settings.EMBEDDING_PROVIDER.lower()
    if provider == "local":
        return LocalEmbeddingClient()
    if provider == "bailian":
        return BailianEmbeddingClient()
    raise ModelNotConfiguredError(
        f"EMBEDDING_PROVIDER 非法值: {provider!r}（可选: local / bailian）"
    )
