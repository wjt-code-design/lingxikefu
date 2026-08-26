# 灵犀评测基线冻结清单（BASELINE）

> 生效：2026-08-26。规则：**四件套一致才可比**（评测集 + 判定脚本 + 检索参数 + 模型）。

## 一、四件套冻结

| 维度 | 冻结值 | 位置 |
|---|---|---|
| 评测集 | 评测问题库.md / ground-truth.md / 口语化评测集.md | hash 见 BASELINE.sha256 |
| 判定脚本 | backend/scripts/eval_faithfulness.py @ e20014e | git commit |
| 检索参数 | RETRIEVAL_TOP_K=5（2026-08-21 降噪 8→5） | backend/app/core/config.py |
| 模型 | glm-4.5-air（CHAT_PROVIDER=zhipu） | backend/.env |

## 二、faithfulness 新基线（2026-08-26，backend 严格判定器）

- qa：64/78 = **82.1%**（目标 ≥85%）
- refuse：7/8 = **87.5%**（目标 ≥90%）
- 引用合法率：3/14 = **21.4%**（目标 ≥95%）
- refuse_qa 3/4（合理拒答 2、误拒答 Q064）；handoff 5/5；chitchat 5/5
- 口径：qa 分母剔除 refuse_qa（=78）；glm-4.5-air + top_k=5 + eval_faithfulness.py@e20014e

## 三、历史口径（退役存档，不可与上表直接对比）

- **8/16 基线**（gen-eval-final-flash-20260816.json）：eval-and-samples/gen_eval.py 宽松标记词（HONEST_MARKS=未收录/转人工/人工客服 + 只验[来源]标记存在）→ qa_false_rate 4.9%、src_compliance 1.0。**95.1% PASS 是这把宽松尺子的结果，与 backend 严格脚本不同源。**
- **flash 复现**（2026-08-26）：backend 严格脚本 + glm-4-flash → qa 78.8% / refuse 12.5% / 引用 5.6%，仅用于同模型归因（证明非模型退化）。

## 四、门禁说明

- 阈值 85% / 90% / 95% 为既有目标契约，**不变**。
- 当前三项未达标 → CI 先 report-only（只报告不阻塞），优化达标后转硬门禁。
- 已知待优化项：Q064 双模型误拒答；引用合法率 21.4% 需专项提升（模型引用可溯源能力）。

## 五、体系缺口（已暴露，待补）

- BASELINE.sha256 原只冻结评测集 hash，未冻结判定脚本/检索参数/模型 → 2026-08-26 起以本文件补齐四件套。
