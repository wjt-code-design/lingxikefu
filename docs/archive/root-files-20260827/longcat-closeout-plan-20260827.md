# LongCat 迁移收尾 · 全量评测验证与 CI 恢复 执行规划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成"全面取消其他平台模型、仅用 LongCat"的收尾——CI 恢复全绿、全量评测验证 Q056/Q082 修复、收口文档。

**Architecture:** ① 用户一次性配置 GitHub Secret LONGCAT_API_KEY（agent 不可接触）；② CI 重跑验证 7/7 jobs（含 LongCat Eval gate sample 20）；③ 经 GitHub Actions API 拉取全量评测 run 结果核对修复成效；④ 仅当全量 qa <85% 或存在失败题时才进入修复分支（按 faithfulness 失败归类协议）；⑤ 收口进度文档与项目内存。

**Tech Stack:** GitHub Actions / GitHub REST API(httpx+truststore) / backend pytest / eval_faithfulness.py(LongCat, sample 20 / full 100) / 项目内存(markdown)

## Global Constraints

- 不执行任何待办项：本计划产出后待用户"开始执行"指令再动工（本轮只规划）。
- push 时机由用户指令决定（推进 CI 需要 push/Re-run 时先征得用户确认）。
- 数字只来自本次真实运行（show-your-work 五连问），不引用旧的 eval 输出作完成声明。
- 本地无 docker daemon：全量 pytest 478 与 Eval gate 的有效验证只能靠 CI（诚实标注本地只验子集）。
- Eval gate 为硬门禁：`qa ≥ 85%`、`refuse ≥ 90%`（sample 20），未配 LONGCAT_API_KEY 时 fail-closed 判红。
- GitHub API 桥接（已在 2026-08-27 实测可用）：`git credential fill` 提取 token + `truststore.inject_into_ssl()` + `trust_env=False` + `follow_redirects=True`。
- 评测口径：qa 分母剔除 refuse_qa；引用合法率分母为实际引用次数；口径变化必须如实标注。

---

### Task 1: 用户配置 GitHub Secret LONGCAT_API_KEY（前置阻塞，agent 不执行）

> 阻塞所有后续任务：Eval gate fail-closed，缺 key 必红。此项只能用户操作。

**Files:** 无代码改动；操作 GitHub 仓库 <https://github.com/wjt-code-design/lingxikefu> Settings → Secrets and variables → Actions。

**Interfaces:**
- Consumes: 本地 `backend/.env` 中真实可用的 `LONGCAT_API_KEY`（上一会话已配置并验证）。
- Produces: GitHub Secret `LONGCAT_API_KEY` → CI 各 job 注入 `${{ secrets.LONGCAT_API_KEY }}`。

- [ ] **Step 1: 用户新增 Secret**

操作路径：GitHub 仓库 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`：
- Name: `LONGCAT_API_KEY`
- Secret: 复制本地 `backend/.env` 中 `LONGCAT_API_KEY=` 的真实值（占位符 `ak-__CHANGE_ME__` 无效，Eval gate 会 fail-closed）。
- 保存后无需重启，下一次 Actions run 自动生效。

> 关键点：Secret 名必须与 [ci.yml](file:///c:/Users/33393/WorkBuddy/2026-08-15-00-39-34/.github/workflows/ci.yml#L330-L332) 中 `${{ secrets.LONGCAT_API_KEY }}` 完全一致（大小写敏感）。

- [ ] **Step 2: 值班探查 API 连通性（可选，防自拒）**

在 `backend/.venv` 内直连 LongCat 验证 key 有效性（避免全量评测替你做探活）：

```powershell
.\.venv\Scripts\python.exe -c "import asyncio; from app.core.config import settings; from app.llm_clients.chat import get_chat_client; print(settings.CHAT_PROVIDER, settings.LONGCAT_CHAT_MODEL, 'key_len=', len(settings.LONGCAT_API_KEY or '')); print(asyncio.run(get_chat_client().complete([{'role':'user','content':'hi'}])))"
```

期望输出：`longcat LongCat-2.0 key_len= N` + 一段正常回答。若抛 `ModelNotConfiguredError`（LONGCAT_API_KEY 为空）说明本地 key 未注入；若 401/403 说明 key 失效。

- [ ] **Step 3: 结果移交**

向用户回报：key 已配 / 未配 / 失效。未配或失效 → 阻塞，等待用户处理后再进入 Task 2。

---

### Task 2: CI 重跑并确认 7/7 全绿（LongCat Eval gate 生效）

> 依赖：Task 1 完成。目的：全量回归 478 + 契约 + lint + build + migrations + 前端 + LongCat Eval gate 一次性验证。

**Files:** 无代码改动（若需触发 run：用户 UI 操作或经用户授权的空 commit）。

**Interfaces:**
- Consumes: GitHub Secret LONGCAT_API_KEY（Task 1）。
- Produces: 一条 CI run；其结论（全绿 / 具体红项）驱动 Task 3/4 走向。

- [ ] **Step 1: 触发重跑**

方式 A（推荐，UI 操作）：GitHub 仓库 → `Actions` → 最近一次失败的 run（c0d9470 或 6c9631e 的 push）→ `Re-run all jobs`。
方式 B：agent 经用户确认后 `git commit --allow-empty -m "ci: re-run after LONGCAT_API_KEY secret"` + `git push origin master`。

- [ ] **Step 2: 轮询 run 状态**

提取 token 并查询 run（先列最近的 run 列表）：

```
git credential fill < host=github.com + 空行回车 > → 取 password
```

```python
import httpx, truststore
truststore.inject_into_ssl()
tok = "<上一步拿到的 password>"
r = httpx.get("https://api.github.com/repos/wjt-code-design/lingxikefu/actions/runs?per_page=5",
              headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
              follow_redirects=True, trust_env=False)
for run in r.json()["workflow_runs"]:
    print(run["id"], run["head_sha"][:7], run["status"], run["conclusion"], run["display_title"][:50])
```

期望：最新 run（head_sha=6c9631e）status 从 `queued/in_progress` → `completed`，conclusion 待定。

- [ ] **Step 3: 拉取失败 job 明细（若有红）**

对 `conclusion != "success"` 的 run：

```python
jobs = httpx.get(run["jobs_url"], headers=hdr, follow_redirects=True, trust_env=False).json()
for j in jobs["jobs"]:
    print(j["name"], j["conclusion"])
    for s in j["steps"]:
        if s.get("conclusion") == "failure":
            print("  FAIL:", s["name"], "->", f"{run['jobs_url']}#step:{s['number']}")
```

失败 job 的完整日志（302 到 Azure blob，必须 follow_redirects）：`GET <job 的 logs_url>` 拉取原始文本。

- [ ] **Step 4: 判定**

- 全绿 → 记录结论"CI 7/7 全绿（LongCat Eval gate sample 20 达标）"→ 进入 Task 3。
- 红项若仅剩 Eval gate 且错误为 `ModelNotConfiguredError / 401 / 429` → key 未生效或档位不足，回到 Task 1 排查，不写修复代码。
- 红项为 test/契约/lint → 属新回归：先 `systematic-debugging` 归因，再按 TDD 修（不属本规划默认范围，需暂停询问用户）。

---

### Task 3: 确认全量评测结果（run 33076949101 或重新触发）

> 依赖：Task 2 后执行。上一会话已发起全量评测 run 33076949101（workflow_dispatch full_eval=true），结果未确认。若该 run 已过期/被跳过，按 Step 2 重新触发。

**Files:** 只读 GitHub API / CI 日志；无代码改动。

**Interfaces:**
- Consumes: Eval gate 的 faithful 日志（含逐题判定与汇总）。
- Produces: 全量品质结论（qa / refuse / handoff / chitchat / 引用合法率 / 失败题清单），是 Task 4 的判据。

- [ ] **Step 1: 查询目标 run 状态**

```python
r = httpx.get("https://api.github.com/repos/wjt-code-design/lingxikefu/actions/runs/33076949101", headers=hdr, follow_redirects=True, trust_env=False)
print(r.json().get("status"), r.json().get("conclusion"))
```

- completed + success → Step 2 拉日志。
- completed + failure → 直接看 Eval job 日志汇总（Step 2），可能有失败题使 gate 判红（sample 20 与全量 100 不同）。
- queued/in_progress → 等待后重查；不存在/被取消 → 走 Step 3 重触发。

- [ ] **Step 2: 解析汇总数字（证据式提取）**

拉取 Eval job 日志（logs_url，follow_redirects=True），grep 以下锚点并逐项记录：
- `[INPUT] 问题 100 题`（确认全量而非 sample 20）
- `qa` 行：`qa 通过数/分母=百分比`（分母=全量剔除 refuse_qa 后的口径，历史样本 82）
- `refuse` 行：`refuse 8/8=100%` 类
- `引用合法率` 行（分母=实际引用次数）
- `gate 判定` 行：`PASS / FAIL`
- 失败题明细：`FAILED` 或 `qid` 关联行，收集 Q 编号清单。

输出一张区块表（全部取自该日志的本次真实数字）：

```
qa N/M=P%  | refuse R/S=Q%  | 引用合法 x/y  | 失败题 [Q...]
```

- [ ] **Step 3（条件）：run 无效时重新触发全量**

经用户确认后，仓库 `Actions` → `Eval` workflow → `Run workflow` → `full_eval: true`。约 28 分钟–1h（LongCat 无 429 实测基线）。等待完成后重复 Step 1-2。

- [ ] **Step 4: 修复成效判定（针对 Q056/Q082）**

日志中定位 Q056（企业抬头开专票）与 Q082（未实际使用可无理由退）：
- 两句断言均已转述 + 带 `[来源N]` 引用 → 漏断言修复确认。
- 任一仍失败 → 记入 Task 4 失败题清单。

- [ ] **Step 5: 结果移交**

向用户回报全量表 + 结论（达标 / 未达标 + 失败题）。未达标进入 Task 4，达标则跳 Task 4 直达 Task 5。

---

### Task 4（条件执行）: 全量失败题修复（qa <85% 或存在失败题时才触发）

> 触发条件：Task 3 结论含失败题 或 qa 口径 <85%（门禁红线）。无失败题则整体跳过。

**Files:** 修复面取决于归类结果——只允许触及 `backend/app/prompts/qa_prompt.py`（生成侧）与 `backend/scripts/eval_faithfulness.py` 的 judge 判据（归为 judge 假阳性时）；检索缺料信号时才考虑 `scripts/demo_data/` 或知识库文档。

**Interfaces:**
- Consumes: Task 3 的失败题 Q 清单 + GT 断言。
- Produces: 修复 commit + 本地子集转绿证据，交由 CI 全量复验。

- [ ] **Step 1: 失败归类（对照法，禁止盲改）**

对每道失败题，取 `评测问题库.md` 的 GT 断言 vs 模型回答逐条对照，归入四类：
- 缺断言（回答漏掉某兄弟句/场景条款）→ 修 [qa_prompt.py](file:///c:/Users/33393/WorkBuddy/2026-08-15-00-39-34/backend/app/prompts/qa_prompt.py) 要点完整性规则（复用规则 10 的并列条款句式扩充，参考 d850f78 先例）。
- 泛化（只答主结论）→ 同上。
- LLM 自拒（句面相关却报"未收录"）→ 规则 11 加正例（参考 52917d1 先例）。
- judge 假阳性（有 `[来源N]` 仍判拒/判缺）→ 收紧 `_is_llm_refusal()` 判据（参考 3e12323 先例）。

历史失败题起点（全量基线 3fbfb0b 记录，供首轮排查）：Q032(数字未全中)/Q041(窗口25%)/Q086/Q088/Q096(漏"含搬运至楼下")。

- [ ] **Step 2: 每类修复走 TDD 红绿**

- 写失败测试（`backend/tests/test_eval_faithfulness.py` 对应判据或新断言用例）→ 跑红：
  `.\.venv\Scripts\python.exe -m pytest tests/test_eval_faithfulness.py -q -o addopts=""` 期望 FAIL。
- 最小改 `qa_prompt.py` 或 `eval_faithfulness.py` → 跑绿，且 `tests/test_llm_clients.py tests/test_rag.py` 零回归。

- [ ] **Step 3: 本地确定性复验**

```powershell
.\.venv\Scripts\python.exe -m scripts.eval_faithfulness --sample 20 --kb-name "星河智家·官方政策库"
```

期望：qa ≥85%、refuse ≥90%、失败题消失或减少。记录数字进入 commit message（不得引用旧输出）。

- [ ] **Step 4: 提交（单 commit，范围只含目标文件）**

```bash
git add backend/app/prompts/qa_prompt.py backend/scripts/eval_faithfulness.py backend/tests/test_eval_faithfulness.py
git commit -m "fix(eval): 治愈全量失败题（<问题类型>），sample20 qa <新百分比>"
```

- [ ] **Step 5: 复跑 CI（回 Task 2 Step 1-4）**

经用户确认 push 后重跑；Eval gate（LongCat sample 20）PASS 且 7/7 全绿 → 标记 Task 4 完成。

---

### Task 5: 收口——文档同步与内存固化

> 依赖：Task 2 全绿 +（若触发）Task 4 完成。

**Files:**
- Modify: `C:\Users\33393\Desktop\Lingxi-Remaining-Tasks-Plan.md`（状态表回填）
- Modify: `c:\Users\33393\.trae-cn\memory\projects\-c-Users-33393-WorkBuddy-2026-08-15-00-39-34--p2-0af2464fcd00206380a7\project_memory.md`（追加结论）

**Interfaces:**
- Consumes: Task 3 汇总表 + Task 4 修复数字 + CI 结论。
- Produces: 可留档的最终状态记录。

- [ ] **Step 1: 回填桌面计划文件**

在"当前进度总览"表中把 Wave 1-4 全标 ✅（证据：6381cd5/2afd3bf/cf01d9d/5c57c45/ce20f4a 已提交）；"待用户处理"表更新为只剩 GitHub Secret LONGCAT_API_KEY 一项（ZHIPU_API_KEY 行删除，其余已验证解决）。

- [ ] **Step 2: 追加项目内存结论**

新条目标题 `## Lessons Learned - LongCat 收尾闭环（2026-08-27）`，内容：CI 7/7 全绿（含 LongCat Eval gate 数值）、全量评测结论（qa 口径/失败题 Q 清单/引用合法率）、修复 commit 号、残留限制（评测模型 LongCat ≠ 未来可能的其他模型）。

- [ ] **Step 3: 终态确认（show-your-work 五连问）**

逐问自答并存档：① 全绿数字来自哪次 run（id+日期）？② 能否复现（命令）？③ 失败测试有没有眼见过红？④ 评测判据与生产口径是否独立？⑤ 已知限制（本地只验子集、评测=生产同模型等）？

---

## Self-Review

- **Spec 覆盖**：本规划覆盖全部真实待办——Secret 配置（T1）、CI 恢复与验证（T2）、全量评测确认（T3）、失败题修复（T4 条件）、文档收口（T5）。旧计划文件 Wave 1-4 经 git log 核实已全部提交（6381cd5/2afd3bf/cf01d9d/5c57c45/ce20f4a），非本规划范围。
- **无占位符**：每步含确切命令、文件路径、期望输出；T4 的失败题清单以历史基线（3fbfb0b）为起点，归类协议与先例 commit 均明确。
- **类型/引用一致**：`full_eval`/`--kb-name "星河智家·官方政策库"`/`--sample 20` 与 ci.yml 及 eval_faithfulness.py 实参一致；Secret 名与 ci.yml `${{ secrets.LONGCAT_API_KEY }}` 一致。