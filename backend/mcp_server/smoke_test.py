"""MCP Server 协议级冒烟测试（零依赖，仅标准库 + 被测模块）。

覆盖：
  1. JSON-RPC 层：initialize / tools/list / tools/call / ping / 未知方法 / 通知
  2. 工具语义层：rrf_fuse 融合公式、verify_citations 阈值、self_check 双闸门、diagnose 归因
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.server import TOOLS, handle_message  # noqa: E402

FAILED = False


def check(name: str, cond: bool, extra: str = "") -> None:
    global FAILED
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILED = True


def rpc(method, params=None, req_id=1):
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return handle_message(msg)


def call(name, args, req_id=9):
    r = rpc("tools/call", {"name": name, "arguments": args}, req_id)
    if r is None or "result" not in r:
        return None
    txt = r["result"]["content"][0]["text"]
    return json.loads(txt)


print("=== 1. JSON-RPC / MCP 协议层 ===")
init = rpc("initialize", {"protocolVersion": "2024-11-05",
                          "capabilities": {},
                          "clientInfo": {"name": "test", "version": "1.0"}})
check("initialize 返回 protocolVersion", init["result"]["protocolVersion"] == "2024-11-05")
check("initialize 声明 tools 能力", "tools" in init["result"]["capabilities"])
check("serverInfo 有 name/version", init["result"]["serverInfo"]["name"] == "lingxi-rag")

lst = rpc("tools/list")
tools = lst["result"]["tools"]
check(f"tools/list 返回 4 个工具（实际 {len(tools)}）", len(tools) == 4)
check("每个工具有 name/description/inputSchema",
      all(t.get("name") and t.get("description") and t.get("inputSchema") for t in tools))
check("description 足够长（>60 字，Agent 靠它判断何时调用）",
      all(len(t["description"]) > 60 for t in tools))
check("inputSchema 有 type=object 与 properties",
      all(t["inputSchema"].get("type") == "object" and t["inputSchema"].get("properties") for t in tools))

ping = rpc("ping")
check("ping 返回空 result", ping["result"] == {})
unknown = rpc("no/such/method")
check("未知方法返回 -32601", unknown["error"]["code"] == -32601)
check("通知（无 id）返回 None", handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None)
bad_tool = rpc("tools/call", {"name": "not_exist", "arguments": {}})
check("未知工具返回 -32602", bad_tool["error"]["code"] == -32602)
bad_args = rpc("tools/call", {"name": "rrf_fuse", "arguments": {"wrong": 1}})
check("参数不匹配返回 -32602", bad_args["error"]["code"] == -32602)

print("\n=== 2. rrf_fuse（加权 RRF，k=60, dense2:sparse1）===")
r = call("rrf_fuse", {"dense_ids": ["d1", "d2", "d3"], "sparse_ids": ["s1", "d1"]})
print(f"  融合: {[f['id'] for f in r['fused']]}  score={[f['score'] for f in r['fused']]}")
check("k=60", r["k"] == 60)
check("权重回显 dense=2.0 / sparse=1.0",
      r["weights"]["dense"] == 2.0 and r["weights"]["sparse"] == 1.0)
s_d1 = next(f["score"] for f in r["fused"] if f["id"] == "d1")
s_d2 = next(f["score"] for f in r["fused"] if f["id"] == "d2")
check("双路命中的 d1 > 单路的 d2", s_d1 > s_d2, f"({s_d1} > {s_d2})")
expect = 2 / 61 + 1 / 62
check("d1 分数与公式 2/(k+1)+1/(k+2) 一致", abs(s_d1 - expect) < 1e-6,
      f"({s_d1} ≈ {expect:.6f})")

print("\n=== 3. verify_citations（阈值 0.30，与生产同口径）===")
okr = call("verify_citations", {
    "answer": "保修期为 12 个月。[来源1]",
    "chunk_texts": ["手机等数码产品自签收之日起 12 个月整机保修。"]})
print(f"  正例: {okr}")
check("支撑充分判合法", okr["all_valid"] is True)
check("总数为 1", okr["total_count"] == 1)

badr = call("verify_citations", {
    "answer": "违约金为合同金额的 30%。[来源1]",
    "chunk_texts": ["违约责任按实际损失赔偿。"]})
print(f"  反例: {badr}")
check("支撑不足判非法", badr["all_valid"] is False)
check("给出 overlap 归因", badr["detail"][0]["overlap"] < 0.30)

r0 = call("verify_citations", {"answer": "没有引用。", "chunk_texts": ["任意内容"]})
check("无引用不判非法", r0["all_valid"] is True and r0["total_count"] == 0)
r9 = call("verify_citations", {"answer": "越界。[来源9]", "chunk_texts": ["只有一条。"]})
check("编号越界不计入总数", r9["total_count"] == 0)

print("\n=== 4. self_check_answer（引用 + 数字双闸门）===")
good = call("self_check_answer", {
    "answer": "保修期为 12 个月，电池 6 个月。[来源1]",
    "chunk_texts": ["手机等数码产品自签收之日起 12 个月整机保修，电池保修 6 个月。"],
    "allowed_numbers": ["12", "6"]})
print(f"  正例: verdict={good['verdict']} reasons={good['reasons']}")
check("PASS 判定", good["verdict"] == "PASS")
check("citation_ok / numbers_ok 均为真", good["citation_ok"] and good["numbers_ok"])

badnum = call("self_check_answer", {
    "answer": "保修期为 12 个月，违约金 30%。[来源1]",
    "chunk_texts": ["手机等数码产品自签收之日起 12 个月整机保修。"],
    "allowed_numbers": ["12"]})
print(f"  编造数字: verdict={badnum['verdict']} unexpected={badnum['unexpected_numbers']}")
check("编造数字判 FAIL", badnum["verdict"] == "FAIL")
check("指出 30 这个意外数字",
      any(x.rstrip("%") == "30" for x in badnum["unexpected_numbers"]),
      f"实际 {badnum['unexpected_numbers']}")

skip = call("self_check_answer", {
    "answer": "保修 12 个月。[来源1]", "chunk_texts": ["自签收之日起 12 个月整机保修。"],
    "allowed_numbers": []})
check("未传白名单则跳过数字闸门（numbers_ok=True）",
      skip["numbers_ok"] is True and skip["verdict"] == "PASS")

print("\n=== 5. diagnose_fusion（融合归因）===")
d = call("diagnose_fusion", {"dense_ids": ["d1", "d2"], "sparse_ids": ["s1", "d1"]})
print(f"  {d}")
check("both 正确", d["both"] == ["d1"])
check("dense_only 正确", d["dense_only"] == ["d2"])
check("sparse_only 正确", d["sparse_only"] == ["s1"])
check("给出调权重建议", bool(d["advice"]))

print("\n=== 6. 子进程 stdio 端到端（模拟真实 MCP 客户端）===")
reqs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "c", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "rrf_fuse", "arguments": {"dense_ids": ["a"], "sparse_ids": ["b"]}}},
]
proc = subprocess.run(
    [sys.executable, "-m", "mcp_server.server"],
    input="\n".join(json.dumps(r) for r in reqs) + "\n",
    capture_output=True, text=True, timeout=90, cwd=str(ROOT),
)
lines = [l for l in proc.stdout.strip().split("\n") if l.strip()]
check("stdio 返回 3 条响应（通知不响应）", len(lines) == 3, f"实际 {len(lines)}")
if len(lines) == 3:
    r1, r2, r3 = (json.loads(l) for l in lines)
    check("响应 1 = initialize", "serverInfo" in r1.get("result", {}))
    check("响应 2 = tools/list 且 4 个工具", len(r2["result"]["tools"]) == 4)
    check("响应 3 = tools/call 返回内容", r3["result"]["content"][0]["type"] == "text")
check("stderr 有 ready 提示", "ready" in proc.stderr.lower())

print("\n" + ("❌ 有用例失败" if FAILED else "✅ 全部通过"))
sys.exit(1 if FAILED else 0)
