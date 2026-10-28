"""百炼原生 rerank 客户端（2026-10-27 接入）。

⚠️ 为什么不用 OpenAI 兼容端点：百炼的 rerank **只提供 DashScope 原生接口**。
实测把请求打到 ``{base}/compatible-mode/v1/rerank`` 返回 **HTTP 404 + 空响应体**——
空 body 不像 401（密钥错）/ 429（限流）那样带错误说明，是最难排查的一类故障
（唯一定位手段是"换个地址再试"）。因此 chat/视觉走 compatible-mode，rerank 走
原生路径，两套 URL 与两套 JSON 结构，**不可混用**。

端点（百炼官方文档「通用文本排序模型 API 使用详情」）::

    POST {base}/api/v1/services/rerank/text-rerank/text-rerank

请求体（DashScope 原生嵌套结构）::

    {"model": "qwen3.7-text-rerank",
     "input": {"query": "...", "documents": ["...", "..."]},
     "parameters": {"top_n": 5, "instruct": "..."}}

响应体::

    {"output": {"results": [{"index": 0, "relevance_score": 0.93}, ...]},
     "usage": {"prompt_tokens": 79, "total_tokens": 79},
     "request_id": "..."}

与 chat 的三处结构差异：
1. ``index`` 是**输入 documents 数组的原始下标**，不是排序后位置——必须自己回填
2. ``relevance_score`` 是**本次请求内的相对分**，无跨请求可比性，也**不能直接
   当拒答阈值用**（拒答仍走稠密原始余弦分，见 retrieval_service 的解耦设计）
3. ``return_documents`` **qwen3.7-text-rerank 不支持**（仅 gte-rerank-v2 /
   qwen3-rerank / qwen3-vl-rerank 支持），故默认不传，靠 index 回填原文

🛡️ 为什么用**同步** httpx（而非 chat.py 的共享 AsyncClient）：
管线编排器 ``PipelineRunner.run`` 是**同步**的（节点签名 ``(Pipeline) -> Pipeline``），
且评测链明确禁止「线程 + asyncio.run」——新 loop 复用绑死旧 loop 的连接池
（见 ``app/services/eval_runner.py`` ADR-1 与 chat.py 的 worker own_client 教训）。
同步客户端与线程模型无关，在主线程、线程池、评测子进程三种场景下行为一致。

失败语义：HTTP 错误 → ``RerankError``（携带状态码与响应体摘要）；网络/解析错误
同样 fail-closed 抛出，**绝不静默返回空结果**——静默降级会把"rerank 没生效"
伪装成"检索效果差"，是排查噩梦。（调用方 steps/rerank.py 负责 fail-open 降级。）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.llm_clients.base import ModelNotConfiguredError

logger = logging.getLogger(__name__)

#: rerank 是同步语义但服务端为 HTTP；超时给足（百炼 rerank 单次可达 120K token 输入）
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

#: 百炼 rerank 原生路径（固定后缀，base 只到域名）
_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


class RerankError(RuntimeError):
    """rerank 调用失败（HTTP 错误 / 网络错误 / 响应结构异常）。fail-closed。"""


@dataclass(frozen=True)
class RerankResult:
    """单条重排结果。

    Attributes:
        index: 候选列表中的**原始下标**（回填原文用）
        score: 相关性分数（本次请求内相对分，不可跨请求比较、不可当拒答阈值）
    """

    index: int
    score: float


def _api_key() -> str:
    """取 key：优先 RERANK_API_KEY，回落 DASHSCOPE_API_KEY（百炼官方环境变量名）。"""
    key = (settings.RERANK_API_KEY or "").strip()
    if not key:
        raise ModelNotConfiguredError(
            "rerank 未配置 API Key：请设置 RERANK_API_KEY（或 DASHSCOPE_API_KEY）"
        )
    return key


def _api_url() -> str:
    """拼原生端点 URL。注意 base 只到域名，路径固定。"""
    base = (settings.RERANK_BASE_URL or "").strip().rstrip("/")
    if not base:
        raise ModelNotConfiguredError(
            "rerank 未配置端点：请设置 RERANK_BASE_URL"
            "（如 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com，"
            "勿带 /compatible-mode/v1）"
        )
    return f"{base}{_RERANK_PATH}"


def rerank(
    query: str,
    documents: list[str],
    *,
    top_n: int | None = None,
    instruct: str | None = None,
) -> list[RerankResult]:
    """对候选文档重排（同步）。

    Args:
        query: 用户查询。
        documents: 候选文档原文列表（顺序即原始下标）。
        top_n: 返回条数，默认 ``settings.RERANK_TOP_N``。
        instruct: 排序任务指令，默认 ``settings.RERANK_INSTRUCT``。

    Returns:
        按 relevance_score 降序的 :class:`RerankResult` 列表（每项带原始 index）。

    Raises:
        ModelNotConfiguredError: 未配置 key / 端点。
        RerankError: HTTP 错误、非 200 响应、响应结构异常、空候选。
    """
    if not documents:
        return []
    if not query.strip():
        raise RerankError("rerank query 为空")

    effective_top_n = settings.RERANK_TOP_N if top_n is None else top_n
    effective_top_n = max(1, min(effective_top_n, len(documents)))
    payload: dict = {
        "model": settings.RERANK_MODEL,
        "input": {"query": query, "documents": documents},
        "parameters": {"top_n": effective_top_n},
    }
    # instruct 仅部分模型生效（qwen3.7-text-rerank 支持；gte-rerank-v2 不支持，
    # 传了会 400）——故按模型名决定是否带。
    ins = settings.RERANK_INSTRUCT if instruct is None else instruct
    model = settings.RERANK_MODEL
    if ins and ins.strip() and "gte-rerank" not in model:
        payload["parameters"]["instruct"] = ins.strip()

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(_api_url(), json=payload, headers=headers)
    except httpx.HTTPError as e:
        logger.error("rerank 网络异常: %s", e)
        raise RerankError(f"rerank 请求失败: {e}") from e

    if resp.status_code != 200:
        body = resp.text[:500]
        logger.error("rerank HTTP %s: %s", resp.status_code, body)
        raise RerankError(f"rerank HTTP {resp.status_code}: {body}")

    try:
        data = resp.json()
    except ValueError as e:
        raise RerankError(f"rerank 响应非 JSON: {resp.text[:200]}") from e

    if data.get("code"):
        # 百炼错误体：{"code": "InvalidApiKey", "message": "...", "request_id": "..."}
        raise RerankError(f"rerank 业务错误 {data.get('code')}: {data.get('message')}")

    results = (data.get("output") or {}).get("results")
    if not isinstance(results, list):
        raise RerankError(f"rerank 响应结构异常（缺 output.results）: {str(data)[:200]}")

    out: list[RerankResult] = []
    for item in results:
        idx = item.get("index")
        score = item.get("relevance_score")
        if not isinstance(idx, int) or isinstance(idx, bool) or not isinstance(score, (int, float)):
            logger.warning("rerank 返回项字段异常，跳过: %s", item)
            continue
        out.append(RerankResult(index=idx, score=float(score)))

    # 官方文档保证按 relevance_score 降序返回；此处不依赖该保证，显式再排一次（稳定）
    out.sort(key=lambda r: -r.score)
    return out
