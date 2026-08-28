# RAG 质量深化 · 设计文档（Spec v2，经代码级自审修订）

> 日期：2026-08-28 | 状态：已确认（待实施） | 项目：灵犀 Customer Service
> 出发点：产品能力迭代——以评测度量为基础，深化 RAG 回答质量
> v2 修订：基于 eval_faithfulness.py / qa_prompt.py / refuse.py / run_eval.py / ci.yml 代码级自审，
> 修正 v1 三处过时/缺失假设（详见 §2 自审结论），执行步骤细化到文件/函数/命令级

---

## 1. 背景与问题

### 1.1 评测基线现状（glm-4.5-air + 旧 prompt 时代，2026-08-26 冻结）

| 指标 | 冻结值 | 目标 | 缺口 |
|------|------|------|------|
| qa faithfulness | 82.1%（64/78） | ≥85% | -2.9pp |
| refuse 合理拒答 | 87.5%（7/8） | ≥90% | -2.5pp |
| 引用合法率 | 21.4%（3/14）**（过时数字，见 A1）** | ≥95% | 待新基线确认 |

已知失败点：Q064 双模型误拒答（refuse_qa：资料含答案仍拒答）。

### 1.2 基线已失效（本规划第一动因）

BASELINE.md 规定"四件套一致才可比"（评测集 + 判定脚本 + 检索参数 + 模型）。自冻结以来：
1. **模型**：glm-4.5-air → LongCat-2.0（c0d9470）
2. **判定脚本**：引用统计偏置修复 + 诚实性判据收紧（ae0eb3b）
3. **prompt**：引用贴原文（规则12）、防自拒（规则11）、并列结论逐条转述（规则10）、禁止条款类推（规则13）多轮加固（52917d1、d850f78、ae0eb3b）

结论：旧基线与当前系统不可比，一切优化从重建基线开始。

### 1.3 目标与成功标准

- LongCat 新基线上三项达标：qa ≥85%、refuse ≥90%、引用合法率 ≥95%
- 引用合法率纳入 CI 硬门禁（全量模式判定，见 A2 的样本量决策），指标只升不降有机制保障
- 每道失败题有结构化归因记录（JSON），优化手段可追溯

---

## 2. 自审结论（v1 → v2 修正项）

### A 级（结构性修正）

**A1 · 专项 1 前提修正**：qa_prompt.py 规则 12（[来源N] 引用点句子须贴近原文）正是针对引用合法率的加固，已随 ae0eb3b/52917d1 落地。21.4% 是旧 prompt 下测得——**新基线下 H1（prompt 加固）可能已部分兑现**。因此专项 1 的手段不预设 H1/H2/H3 比例，以新基线逐题归因结果定；prompt 端若新基线已达标则专项 1 直接闭合。

**A2 · citation 门禁样本量陷阱**（v1 遗漏）：eval_faithfulness.py `main()` 的 `pass_all`（L423-427）只判 qa≥85% 与 refuse≥90%，citation 未进 exit code——这是"未纳入门禁"的精确代码位置。但 push 抽样 20 题下引用点仅约 15-30 个，95% 门禁 = 最多错 1 个，单点波动 3-7pp，抖动不可接受。**决策：citation 门禁只在全量模式（workflow_dispatch full_eval）判定，抽样模式只报告不判定**。实现：main() 按 `args.sample == 0` 区分两种模式组装 pass_all。

**A3 · 失败明细无结构化导出**（v1 遗漏）：失败题只打 stdout，且 `answer[:80]` 截断、`res["cit"]` 明细（每个引用点的句子/判定结果）不落盘——归因清单靠手抄 stdout 既低效又丢关键信息。**补建：`--out` JSON 导出**（见 3.1 步骤 1.6）。

### B 级（执行细节补充）

- **B1 · refuse 指标机制定位**：诚实性题流程 = refuse.py 判据（`best_dense < MIN_SCORE=0.30`，config.py L131）→ `_no_llm_reply` 引导语；管线未拒则 LLM 生成 → judge_refuse 判标志词。旋钮精确为四处：MIN_SCORE、refuse.py 判据、qa_prompt 规则 1/5（防编造）、判定器 REFUSE_MARKERS/NEGATION_MARKERS。
- **B2 · 口语化评测口径澄清**：run_eval.py 的口语化集只测检索层 recall@5（不跑 LLM/faithfulness）。阶段一按现有口径跑；口语化 faithfulness 为可选扩展（YAGNI，列入"不做的事"）。
- **B3 · 检索归因工具现成**：run_eval.py 的 T12 分布分析（可答 vs 拒答的 top1/margin 分桶）是专项 2 定 MIN_SCORE 切点的现成工具，直接复用。
- **B4 · 本地/CI 知识库名不一致（隐藏第五维）**：CI 用 `星河智家·官方政策库`（smoke_import 建），本地默认 `星河智家·售后与订单全量库`（seed_demo_data 建）。两者内容同源（kb/ + kb-pdf/）但库名不同——基线口径必须固定一个，**统一用 CI 口径（官方政策库）**，本地跑传 `--kb-name 星河智家·官方政策库`。

### C 级（已确认无需处理）

- eval_one 中 `run_pipeline()` 同步调用阻塞事件循环：评测正确性无影响，不改。
- judge_citations 对空 chunks 返回合法：qa 题必有 chunks（refuse 已过滤），无影响。
- run_eval.py 连接串 `__CHANGE_ME__` 占位符：本地工具，POSTGRES_PASSWORD 环境变量可覆盖，不改。

### D 级（最终审核补充，2026-08-28 v2 终审）

- **D1 · 门禁键修正**：A2 中"`args.sample == 0` 区分模式"不严谨——`--limit N`（无 sample）同样产生子集，会误触 citation 门禁抖动。正确判定键：`full_run = not (args.sample or args.limit or args.offset)`。
- **D2 · 基线 JSON 必须自含 chunks**：归因与判定器双跑都需要"回答 + 对应 chunks"，若 JSON 只存回答则每次归因都要重新检索。`--out` 导出须含每题 chunks（text/score/dense_score/doc_id，约 250KB/100 题）与逐引用点明细（句子/重叠率/判定），使 JSON 成为归因与双跑的单一数据源。
- **D3 · JSON 落盘先于门禁判定**：`--out` 写入必须在 `pass_all` 计算与 exit 之前——全量基线跑在 citation<95% 时 exit 1 属预期，但完整 JSON 必须已落盘。
- **D4 · 调用方兼容**：`_run_faithfulness` 被 admin 评测中心复用（`api/eval.py:165` → `run_faithfulness_eval`），签名只能追加可选参数。
- **D5 · 判定器调参双跑的实现形态**：新增 `--rejudge <json>` 模式对已存 JSON 重跑引用判定（不重检索、不重生成），实现 A 级风险表中"新旧尺子双跑对比留档"的可复现版本；重叠率阈值提为模块常量便于 sweep。
- **D6 · run_eval.py 文档串与代码不符**：docstring 写 `POSTGRES_PASSWORD=...`，代码实际读 `PG_URL`（L69）。命令统一用 `PG_URL`（工具修正不在本次范围）。

---

## 3. 三阶段详细设计（文件/函数/命令级）

### 阶段一：LongCat 基线重建（约 1 天）

| # | 步骤 | 落点 | 产出 |
|---|------|------|------|
| 1.1 | 给 eval_faithfulness.py 加 `--out` JSON 导出 | `backend/scripts/eval_faithfulness.py`：`main()` 与 `_run_faithfulness()` 增加结果收集参数；`eval_one()` 的 `answer` 改全文（stdout 打印仍截断） | 脚本支持结构化导出 |
| 1.2 | 本地全量跑 100 题（LongCat + 新 prompt） | `cd backend && python -m scripts.eval_faithfulness --kb-name "星河智家·官方政策库" --out ../eval-and-samples/results/baseline-longcat-20260828.json`（本地 .env 已配 LongCat，2632b43） | 全量快照 JSON |
| 1.3 | 跑检索层评测 + 口语化（recall@5 口径）+ T12 分布分析 | `POSTGRES_PASSWORD=... python eval-and-samples/run_eval.py` | recall@5、拒答 top1 分布（专项 2 切点依据） |
| 1.4 | 核实 CI 抽样硬门禁状态 | GitHub Actions 最近 run 的 eval job 绿/红 | 门禁状态结论（红 = 抽样与全量有代表性差异，归因清单含此项） |
| 1.5 | 逐题归因：在 JSON 失败清单上逐题标注类别 | 归因四类：`判据过时` / `检索未召回` / `生成不贴原文` / `引用格式问题`；Q064 单独复查 | 归因清单（spec 附录或独立 md） |
| 1.6 | 更新 BASELINE.md / BASELINE.sha256 | 四件套 = 评测集 hash + 判定脚本 commit + RETRIEVAL_TOP_K=5 + LongCat-2.0；修正"先 report-only"过时说法；引用合法率记录新值并注明旧值 21.4% 不可比 | 新基线冻结 |

**退出标准**：新基线 JSON + 归因清单完成，每道失败题有类别标注；BASELINE 冻结。

**红测补充（1.1 同批次）**：`backend/tests/test_eval_faithfulness.py` 补 `--out` 导出的单测（tmp_path 断言 JSON 结构含 qid/kind/ok/why/answer/citations 字段）。

### 阶段二：归因攻坚（3 个专项，按新基线缺口排序；每专项 1-2 天批次）

**专项 1：引用合法率（目标 ≥95%）**

先看新基线：若已达标 → 直接闭合进阶段三；未达标则按归因类别走：

| 归因 | 手段 | 落点 |
|------|------|------|
| 引用格式问题（缺 [来源N]/错位） | prompt few-shot 强化 + 生成后校验兜底（管线层检测"有事实句无引用"时 retry 一次） | `qa_prompt.py` 规则 3/6 强化；可选 `steps/generate.py` 后处理 |
| 生成不贴原文（改写致交集 <30%） | 逐样本核对引用点句子 vs chunk：确属实质支撑被误判 → 判定器 H2 调参（窗口/阈值），**必须新旧尺子双跑对比留档 + 重冻结判定脚本**；确属改写 → prompt 规则 12 加反例 | `eval_faithfulness.py::_sentence_supported` / `qa_prompt.py` |
| 检索未召回（chunk 不含支撑句） | 并入专项 3 检索层处理，专项 1 不动检索 | — |

纪律：prompt、判定器、检索三处**禁止同批次同时变化**（单一变量归因）。

**专项 2：拒答判据（目标 ≥90%，含 Q064）**

- 从新基线 refuse/refuse_qa 失败题出发，先跑 B3 分布分析定位：
  - 误拒答（资料含答案仍拒）且 top1 ≥0.30 → MIN_SCORE 偏高或 judge 边界 → `refuse.py` 判据精细化（如 margin 辅助）或 MIN_SCORE 微调（同步重冻结）
  - 误拒答且 top1 <0.30 → 检索未召回 → 查 rewrite（`query_rewrite.py`）与该题期望来源，必要时补 KB 内容锚点（沿 cbfb14f 先例）
  - 漏拒答（该拒没拒）→ judge_refuse 判据与 qa_prompt 规则 1/5 成对检查——**每次改动要么判定器与 prompt 同批成对改，要么单改一端并在归因清单注明**，防互相掩蔽
- 红测先行：Q064 及新失败题先落 `test_eval_faithfulness.py` 断言用例

**专项 3：faithfulness（目标 ≥85%）**

- 完全由新基线归因清单驱动，逐题修复 → 子集快跑（`--sample 20` 或失败题 `--limit/--offset`）→ 全量回归
- 已沉淀手段复用：并列结论逐条转述（规则 10）、禁止条款类推（规则 13）、防自拒正例（规则 11）
- 检索类失败：评估 rerank / 检索参数——若需动 top_k=5 则按四件套纪律整体重冻结

**每专项收尾**：全量 100 题回归（`--out` 导出对比上一基线），任何指标下降即回退该批次（git bisect）。

### 阶段三：门禁补全与二次冻结（约半天）

现状（已核实）：eval job 已是硬门禁（qa≥85% + refuse≥90%，抽样 20 题）；**citation 未进判定**。

| # | 步骤 | 落点 |
|---|------|------|
| 3.1 | citation ≥95% 进全量模式 pass_all：`args.sample == 0` 时 `pass_all and= (cit_total == 0 or cit_good/cit_total >= 0.95)`；抽样模式维持只报告 | `eval_faithfulness.py::main()` L423-427；`[RESULT]` 打印语同步说明两种模式 |
| 3.2 | CI 全量入口的注释与 job 名同步（Eval gate (hard) 含 citation@full_eval） | `.github/workflows/ci.yml` eval job 注释 |
| 3.3 | 单测：pass_all 两种模式判定（抽样不判 citation / 全量判） | `test_eval_faithfulness.py` |
| 3.4 | 手动 full_eval 全量复核（约 1h）→ BASELINE 二次冻结（达标值为新下限） | GitHub Actions workflow_dispatch |

---

## 4. 测试与验证纪律（贯穿全程）

1. **红测先行**：每道失败题先落 `test_eval_faithfulness.py` 可复现断言（判定器纯函数可直接单测；eval_one 路径 mock chat client）
2. **全量回归**：每专项收尾全量 100 题 + `--out` JSON 对比上一基线，任一指标下降即回退该批次
3. **台账纪律**：每批次末亲测 junitxml 计数
4. **四件套变更留痕**：判定脚本或检索参数任何变动 → 当次更新 BASELINE.md，注明旧值不可比 + 新旧双跑对比数据

## 5. 不做的事（YAGNI）

- 不扩评测集规模、不接新模型/新评测 LLM（避免再动四件套）
- 不做口语化 faithfulness 口径（口语化维持检索层 recall@5 现状）
- 不动多智能体架构、不做前端改动
- 不做检索参数大调（top_k=5 有降噪调参依据）；rerank 仅在专项 3 检索类失败占比高时评估

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 引用合法率新基线仍远低于 95% | 专项 1 长期不达标 | 先按归因数据判断是否模型能力上限；若 H2 证据充分调判定器（双跑留档），仍不达则在 BASELINE.md 记实证、提出目标修订建议（用户确认，不自行降标） |
| LongCat 输出抖动 | 单次全量波动 | eval_one_retry 已有 429/超时重试；关键批次跑两次取一致结果再冻结 |
| 判定器改动被质疑"改尺子凑分" | 指标可信度受损 | H2 类改动必须先展示"实质支撑但判不合法"具体样本 + 新旧尺子全量双跑对比留档 |
| 全量评测约 1h 迭代慢 | 攻坚效率低 | 开发期失败题子集快跑（`--limit/--offset/--sample`），收尾才全量 |
| 本地与 CI 环境/KB 名差异导致快照不可比 | 基线口径漂移 | B4：统一 CI 口径 KB 名；首次本地全量与 CI full_eval 各跑一次交叉核对 |

## 7. 文档同步（顺带项，阶段一收尾时）

- `docs/multi-agent-collaboration-design.md`：状态改"已实施"（Router/TicketAgent/ImageAgent/SharedContext 已接入 chat.py L313-325）
- `docs/optimization-top3-todo.md`：头部标注 T1/T2/T3 已完成（证据：admin.py topic_dist、ci.yml migrations job、MessageBubble tool 徽标）
