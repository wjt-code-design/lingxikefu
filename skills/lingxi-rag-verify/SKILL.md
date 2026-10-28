---
name: lingxi-rag-verify
description: 校验 AI 回答的引用是否真的被来源支撑、数字是否编造，并做两路检索结果的融合诊断。当需要判断一段 RAG 回答「引文是否编造」「数字是否可信」「为什么这条排在前面」，或需要在写 RAG 检索代码前确认融合口径时使用。
---

# 灵犀 · RAG 回答确定性校验

把灵犀生产环境的**确定性闸门**（零 LLM、结果可复现）封装成可复用技能，
让 AI 编程工具在生成或审阅 RAG 回答时能自证依据，而不是"看起来通顺"就算过。

## 何时用

- 审阅一段带 `[来源N]` 的 RAG 回答 → 判断引文是否编造
- 检查模型是否编造了金额、日期、法条编号等数字
- 调试混合检索排序 → 为什么这条排在前面
- 写检索代码前确认加权 RRF 的口径

## 前置

MCP Server 已在本地运行（stdio）：

```bash
cd backend
python -m mcp_server.server          # stdio
python -m mcp_server.server --http 8009   # streamable-http
```

工具清单（4 个）：

| 工具 | 作用 | 关键口径 |
|---|---|---|
| `verify_citations` | 引用是否被来源支撑 | 2 字窗口交集 ≥ **0.30**；引用点取标记前最近一句 |
| `self_check_answer` | 引用 + 数字白名单双闸门 | 返回 `verdict: PASS/FAIL` 与 `reasons` |
| `rrf_fuse` | 两路召回加权融合 | `k=60`，dense 2 : sparse 1 |
| `diagnose_fusion` | 融合结果归因 | 分 `both` / `dense_only` / `sparse_only` |

## 用法

### 1. 审一段回答（最常用）

```python
from mcp_server.server import self_check_answer

result = self_check_answer(
    answer="保修期为 12 个月，电池保修 6 个月。[来源1]",
    chunk_texts=["手机、平板、笔记本等数码产品自签收之日起 12 个月整机保修，电池 6 个月。"],
    allowed_numbers=["12", "6"],
)
# {"citation_ok": True, "numbers_ok": True, "verdict": "PASS", ...}
```

### 2. 引用编造的典型反例

```python
self_check_answer(
    answer="违约金为合同金额的 30%。[来源1]",
    chunk_texts=["违约责任按实际损失赔偿。"],   # 支撑不了 30%
)
# citation_ok=False，reasons 里会指出 overlap 多少
```

### 3. 融合排序归因

```python
from mcp_server.server import diagnose_fusion
diagnose_fusion(dense_ids=["d1", "d2"], sparse_ids=["s1", "d1"])
# both=["d1"], dense_only=["d2"], sparse_only=["s1"]
```

## 判读规则（重要，别误用）

1. **`verdict=PASS` 只代表"引用与数字站得住"，不代表"语义完全正确"。**
   存在性 + 词面支撑 ≠ 语义支撑。语义支撑率需另外用 Claim 级核验评估
   （法智项目的 F5 校准体系：385 对得支撑率 56.8%，并用 30 对独立复核量化裁判偏差）。
2. **数字白名单要显式传入**。不传就跳过数字闸门，`numbers_ok` 恒为 True —— 别误读成"没有编造数字"。
3. **`overlap` 在 0.30 附近属灰区**。0.25~0.35 建议人工看，不要机械判 PASS/FAIL。
4. **引用编号越界不计入总数**（编号 > 来源数），这与生产落库口径一致。

## 为什么要做成 MCP 工具而不是脚本

- 工具描述里带着**阈值与口径**（0.30、k=60、dense 2:1），Agent 不会凭直觉乱用
- 直接 import 生产代码，**工具行为与线上评测口径同源** —— 不会出现"脚本说合法、线上说非法"
- 零外部依赖（不碰数据库/大模型/网络），任何机器上都能跑
