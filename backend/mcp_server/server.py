"""
灵犀 · MCP Server（Model Context Protocol）—— 零依赖实现
=========================================================

把灵犀的**确定性检索与引用校验能力**暴露为标准 MCP 工具，供 Claude Code / Cursor /
其他支持 MCP 的 AI 编程工具直接调用。

三个设计决策：

1. **零第三方依赖** —— MCP 协议本质是 JSON-RPC 2.0 over stdio，本文件只用标准库。
   好处：任何机器 `python -m mcp_server.server` 就能起来，不受依赖地狱影响；
   对开源也友好（不引入供应链风险）。
   （注：`mcp` 官方 SDK 也能用，但会拉入 cryptography/cffi 等重依赖，
     在部分环境装不上 C 扩展时反而跑不起来。）

2. **不重写业务逻辑** —— 直接 import 生产代码（`retrieval_service._rrf_fuse`、
   `eval_faithfulness.judge_citations`），保证工具行为与线上/评测口径**完全一致**；
   任何口径漂移都会让「工具可用」变成「工具说谎」。

3. **零外部依赖可跑** —— 默认只做纯函数计算（不需要数据库 / 大模型 / 网络）。

运行：
    python -m mcp_server.server              # stdio（Claude Code / Cursor 默认）
    python -m mcp_server.server --http 8009  # streamable-http（实验性，供其他客户端接入）
    python mcp_server/smoke_test.py          # 自检（不需要任何客户端）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

# 允许 `python mcp_server/server.py` 与 `python -m mcp_server.server` 两种启动方式
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── 复用生产实现（口径唯一真源）────────────────────────────────────────
from app.services.retrieval_service import _RRF_K, _rrf_fuse  # noqa: E402
from scripts.eval_faithfulness import judge_citations  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "lingxi-rag"
SERVER_VERSION = "1.0.0"


def _as_chunk_objs(chunk_texts: list[str]) -> list[Any]:
    """把字符串来源列表适配成生产代码期望的 chunk 对象（需带 .text 属性）。

    生产的 judge_citations 内部读 chunks[n-1].text；这里只做输入适配，
    **不重写判定逻辑**，保证工具与线上/评测口径同源。
    """
    from types import SimpleNamespace

    return [SimpleNamespace(text=str(x)) for x in (chunk_texts or [])]


# ══ 工具实现 ══════════════════════════════════════════════════════════
def tool_rrf_fuse(
    dense_ids: list[str],
    sparse_ids: list[str],
    top_k: int = 5,
    w_dense: float = 2.0,
    w_sparse: float = 1.0,
) -> dict[str, Any]:
    """对稠密向量与稀疏词面两路召回结果做加权 RRF 融合排序。

    RRF（Reciprocal Rank Fusion）只依赖**名次**而非分值绝对大小，天然规避
    稠密余弦分（∈[-1,1]）与 BM25（无上界）量纲不一致的问题。
    融合公式：score(doc) = Σ_path w_path / (k + rank_path + 1)，本项目 k=60。
    加权（dense 2 : sparse 1）的原因：纯等权会让「两路都命中的噪声 chunk」
    总分压过「dense 强命中但 sparse 未命中」的真答案。
    """
    # 适配层：生产的 _rrf_fuse 接受 Qdrant ScoredPoint（用 .id / .payload）。
    # RRF 只依赖**名次**不依赖分值，故这里只需提供 id，payload 置空即可。
    # 仍然调用生产实现 —— 不重写融合逻辑，避免"工具口径与线上口径漂移"。
    from types import SimpleNamespace

    dense_hits = [SimpleNamespace(id=str(x), payload={}) for x in dense_ids]
    sparse_hits = [SimpleNamespace(id=str(x), payload={}) for x in sparse_ids]
    fused = _rrf_fuse(
        dense_hits, sparse_hits, top_k, w_dense=w_dense, w_sparse=w_sparse
    )
    return {
        "fused": [{"id": pid, "score": round(score, 6), "meta": meta} for pid, score, meta in fused],
        "k": _RRF_K,
        "weights": {"dense": w_dense, "sparse": w_sparse},
    }


def tool_verify_citations(answer: str, chunk_texts: list[str]) -> dict[str, Any]:
    """校验回答中 [来源N] 引用是否真的被对应来源支撑（防「引文编造」）。

    判定口径与灵犀评测脚本、生产落库规则完全一致：
    - 取每个 [来源N] 标记**之前最近一个句子**（按 。！？； 断句）作为引用点句子；
    - 计算该句子与 chunks[N-1] 的 2 字窗口交集比例（bigram overlap）；
    - 交集比例 >= 0.30 判为「被支撑」，否则判为「引用与来源不匹配」；
    - 编号越界（1 <= N <= len(chunks) 不成立）不计入总数；
    - 连续引用 [来源1][来源2] 共享同一句子时，第二个引用点跳过不计。

    注意：这是**存在性与词面支撑性**校验，不等于语义正确性 —— 语义支撑率需
    另外用 Claim 级核验评估（法智项目的 F5 校准体系）。
    """
    detail: list[dict[str, Any]] = []
    all_valid, _ok_count, total = judge_citations(answer, _as_chunk_objs(chunk_texts), detail)
    return {
        "all_valid": bool(all_valid),
        "total_count": total,
        "detail": detail,
    }


def tool_self_check_answer(
    answer: str, chunk_texts: list[str], allowed_numbers: list[str] | None = None
) -> dict[str, Any]:
    """对一段 AI 回答跑完整的确定性自检：引用是否合法 + 是否有编造数字。

    两道闸门都是零 LLM 纯函数，结果可复现：
    1. 引用合法性：每个 [来源N] 是否被对应 chunk 支撑（bigram overlap >= 0.30）；
    2. 数字白名单：回答中出现的数字是否都在允许清单内 —— 防止模型编造
       金额、日期、法条编号等事实。

    未传 allowed_numbers 时会跳过数字闸门（numbers_ok 恒为 True）。
    """
    import re

    detail: list[dict[str, Any]] = []
    all_valid, _ok, total = judge_citations(answer, _as_chunk_objs(chunk_texts), detail)

    reasons: list[str] = []
    if not all_valid:
        bad = [d for d in detail if not d.get("ok")]
        reasons.append(
            f"{len(bad)}/{total} 处引用与来源不匹配："
            + "；".join(f"[来源{d['n']}] overlap={d['overlap']}" for d in bad[:5])
        )

    unexpected: list[str] = []
    numbers_ok = True
    allowed = {str(x).strip() for x in (allowed_numbers or [])}
    if allowed:
        stripped = re.sub(r"\[来源\d+\]", "", answer)
        nums = re.findall(r"\d+(?:\.\d+)?%?", stripped)
        unexpected = sorted({n for n in nums if n.rstrip("%") not in allowed and n not in allowed})
        numbers_ok = not unexpected
        if unexpected:
            reasons.append(f"出现不在白名单内的数字：{unexpected[:8]}")

    return {
        "citation_ok": bool(all_valid),
        "citation_detail": detail,
        "numbers_ok": numbers_ok,
        "unexpected_numbers": unexpected,
        "verdict": "PASS" if not reasons else "FAIL",
        "reasons": reasons,
    }


def tool_diagnose_fusion(dense_ids: list[str], sparse_ids: list[str]) -> dict[str, Any]:
    """诊断两路召回的融合结果：哪些是「双路都命中」、哪些是「单路命中」。

    用于回答「为什么这条排在前面」这类问题：
    - both        两路都命中的文档（噪声 chunk 常在此，等权下易压过真答案）
    - dense_only  仅语义命中（强语义但词面未覆盖的真答案常在此）
    - sparse_only 仅词面命中
    """
    d, s = list(dense_ids), list(sparse_ids)
    sset = set(s)
    both = [x for x in d if x in sset]
    dense_only = [x for x in d if x not in sset]
    sparse_only = [x for x in s if x not in set(d)]

    advice = ""
    if len(both) > len(dense_only):
        advice = (
            "双路命中数量偏多：噪声 chunk 容易在等权下压过单路的强语义命中，"
            "建议提高稠密权重（w_dense 2→3）后用同一评测集复跑 A/B 再定夺，"
            "不要只看单次结果。"
        )
    elif dense_only:
        advice = "存在仅语义命中的文档：确认加权后它们没有被双路噪声压过。"

    return {
        "both": both,
        "dense_only": dense_only,
        "sparse_only": sparse_only,
        "advice": advice,
    }


# ══ 工具注册表 ════════════════════════════════════════════════════════
def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "rrf_fuse",
        "description": (
            "对稠密向量与稀疏词面两路召回结果做加权 RRF 融合排序。RRF 只依赖名次而非分值绝对大小，"
            "天然规避稠密余弦分与 BM25 量纲不一致的问题。公式 score=Σ w/(k+rank+1)，本项目 k=60、"
            "dense:sparse=2:1（等权会让双路命中的噪声 chunk 压过 dense 强命中的真答案）。"
        ),
        "inputSchema": _schema(
            {
                "dense_ids": {"type": "array", "items": {"type": "string"},
                              "description": "稠密检索结果 id，按相关性降序"},
                "sparse_ids": {"type": "array", "items": {"type": "string"},
                               "description": "稀疏检索结果 id，按相关性降序"},
                "top_k": {"type": "integer", "default": 5, "description": "返回条数"},
                "w_dense": {"type": "number", "default": 2.0, "description": "稠密路权重"},
                "w_sparse": {"type": "number", "default": 1.0, "description": "稀疏路权重"},
            },
            ["dense_ids", "sparse_ids"],
        ),
        "fn": tool_rrf_fuse,
    },
    {
        "name": "verify_citations",
        "description": (
            "校验回答中 [来源N] 引用是否被对应来源支撑（防引文编造）。口径与生产评测一致："
            "取标记前最近一句，算与来源文本的 2 字窗口交集比例，>= 0.30 判为被支撑；"
            "编号越界不计入总数。这是存在性与词面支撑校验，不等于语义正确性。"
        ),
        "inputSchema": _schema(
            {
                "answer": {"type": "string", "description": "含 [来源N] 标记的回答文本"},
                "chunk_texts": {"type": "array", "items": {"type": "string"},
                                "description": "来源文本列表，索引 0 对应 [来源1]"},
            },
            ["answer", "chunk_texts"],
        ),
        "fn": tool_verify_citations,
    },
    {
        "name": "self_check_answer",
        "description": (
            "对一段 AI 回答跑完整确定性自检：引用合法性 + 数字白名单双闸门（均为零 LLM 纯函数）。"
            "返回 verdict=PASS/FAIL 与 reasons。未传 allowed_numbers 时跳过数字闸门。"
        ),
        "inputSchema": _schema(
            {
                "answer": {"type": "string", "description": "待检查的回答文本"},
                "chunk_texts": {"type": "array", "items": {"type": "string"},
                                "description": "来源文本列表，索引 0 对应 [来源1]"},
                "allowed_numbers": {"type": "array", "items": {"type": "string"},
                                    "description": "允许出现的数字清单，如 [\"12\",\"6\"]；空则跳过数字闸门"},
            },
            ["answer", "chunk_texts"],
        ),
        "fn": tool_self_check_answer,
    },
    {
        "name": "diagnose_fusion",
        "description": (
            "诊断两路召回的融合结果：区分 both（双路都命中，噪声常在此）/ dense_only（仅语义命中，"
            "真答案常在此）/ sparse_only（仅词面命中），并给出是否该调权重的建议。"
        ),
        "inputSchema": _schema(
            {
                "dense_ids": {"type": "array", "items": {"type": "string"}},
                "sparse_ids": {"type": "array", "items": {"type": "string"}},
            },
            ["dense_ids", "sparse_ids"],
        ),
        "fn": tool_diagnose_fusion,
    },
]

_TOOL_MAP: dict[str, Callable[..., Any]] = {t["name"]: t["fn"] for t in TOOLS}


# ══ JSON-RPC / MCP 协议层 ═════════════════════════════════════════════
def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """处理一条 JSON-RPC 请求；通知（无 id）返回 None。"""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if req_id is None:  # 通知，不响应
        return None

    if method == "initialize":
        return _ok(
            req_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "tools/list":
        return _ok(
            req_id,
            {
                "tools": [
                    {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                    for t in TOOLS
                ]
            },
        )

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = _TOOL_MAP.get(name)
        if fn is None:
            return _err(req_id, -32602, f"unknown tool: {name}")
        try:
            out = fn(**args)
            return _ok(
                req_id,
                {
                    "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}],
                    "isError": False,
                },
            )
        except TypeError as e:
            return _err(req_id, -32602, f"invalid arguments: {e}")
        except Exception as e:  # noqa: BLE001
            return _ok(
                req_id,
                {
                    "content": [{"type": "text", "text": f"tool error: {e}"}],
                    "isError": True,
                },
            )

    if method == "ping":
        return _ok(req_id, {})

    return _err(req_id, -32601, f"method not found: {method}")


def serve_stdio() -> None:
    """stdio 传输：每行一个 JSON-RPC 消息。"""
    sys.stderr.write(f"[{SERVER_NAME}] MCP server ready on stdio "
                     f"({len(TOOLS)} tools: {', '.join(_TOOL_MAP)})\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def serve_http(port: int, host: str) -> None:
    """极简 streamable-http：POST /mcp 收 JSON-RPC，返回 JSON。"""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            resp = handle_message(msg)
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8") if resp else b""
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a: Any) -> None:
            pass

    srv = HTTPServer((host, port), Handler)
    sys.stderr.write(f"[{SERVER_NAME}] MCP http server on http://{host}:{port}/mcp\n")
    sys.stderr.flush()
    srv.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="灵犀 MCP Server（零依赖）")
    ap.add_argument("--http", type=int, default=0, help="streamable-http 端口；0 表示 stdio")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if args.http:
        serve_http(args.http, args.host)
    else:
        serve_stdio()


if __name__ == "__main__":
    main()
