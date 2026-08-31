# 工作交接文档：灵犀智能客服系统（2026-08-31）

> 交接对象：接手本项目的 AI 助手。本文档假设你零上下文——所有路径/命令/状态截至 2026-08-31，master @ `0575c14`，CI #71 绿。

---

## 一、项目是什么

面向 3C 数码与家电电商的智能客服：**FastAPI + React + Qdrant + Redis + LongCat LLM**。

核心链路：规则式 Router 前置分流 → 自研轻量 RAG 管线（6 节点：意图→改写→缓存→hybrid 检索→拒答→生成）→ SSE 流式应答。外围：工单自动化、坐席工作台（5 页面）、通知中心、订单工具、管理后台。单租户部署（6381cd5 安全裁定钉死）。

## 二、当前状态（全部已核实）

- **仓库**：master @ `0575c14`，工作区干净，与 origin 同步，CI #70/#71 连续绿。
- **测试**：679 passed / 8 skipped / 0 failed（8 skip = PG 密码认证 5 + reportlab 缺失 2 + 既有标记 1，均为环境因素）。
- **ruff**：0.16.4，零 error（扫 `app tests alembic scripts`）。
- **迁移**：最新 head = 0020（alembic 对称已验证）。
- **契约**：`generate_openapi.py` + `check_contracts.py` PASS 无新增漂移。
- **评测基线（CI 全量 100 题权威口径）**：qa 93.8% / refuse 8/8 = 100% / citation 97.9%，PASS（LongCat 充值后实测，存档 `eval-and-samples/results/longcat-refill-verify-20260830.json`）。

## 三、两天已完成的工作（08-29/30）

### 脉络

1. **评测攻坚**（08-28，Task 4-6 + S4）：LongCat 基线冻结 → 逐题归因 → 判定器 bug 修复 → prompt 拒答边界两轮补丁 → CI 首跑 FAIL 后发现 Q042 场景归类真缺陷（受控实验铁证）与本地评测库污染事件（seed 脚本导入租户最新库致假绿），全部修复。
2. **外部审查闭环**：桌面审查报告 15 条经运行级交叉验证后处置——7 修 / 4 证伪不修 / 3 挂账 / L5 清理。结论：外部报告行号准但修法常没读底层实现，"先核实再修"是必须纪律。
3. **闭环架构方案 v2.1**（经红队击穿修订，docs/superpowers/specs/2026-08-29-closed-loop-architecture.md）→ 三期全部落地：
   - **零期+一期**（5 任务）：语义缓存极性防护、quick 失效面、工单移交摘要、降级话术阶梯、配额 DB 化
   - **二期**（3 任务）：L2 预起草基建、坐席填入通道角色修正、意图影子采样（20% 落 meta 不驱动路由）
   - **三期**（3 任务）：信号聚类升级（hot_gaps 时间窗 + feedback_gaps）、quick 版本持久化 Redis、KB 发布门禁 v1（EvalResult 绑 kb_version + gate 端点）
4. **批次 H**（4 任务）：订单号 Unicode 边界修复（贴汉字单号 6 消费方修复）、配额双检+connect_timeout、语义缓存同义否定极大类归并、观测性打包
5. **门禁 v2**（3 任务）：chunk 级 visible staged 通道 + 回填脚本对账、batch 状态机+抽样快检+翻转发布/回滚（0020）、观测列表端点
6. **批次 I**：数据期决策门槛文档 + 周检查清单 + 四期立项说明
7. **深度扫查**（08-31）：并发/状态/降级 + 死代码 + 口径交叉核数，无 Critical/Major 炸雷

### 累计

18 个实现任务、23 次独立审查全 Approved、测试 519→679、全部推送。

## 四、工作纪律与方法论（必须遵守）

1. **四件套一致才可比**：评测集（hash）+ 判定脚本（hash）+ 检索参数 + 模型 + 评测 KB，任何一项变更必须在 `eval-and-samples/BASELINE.sha256` 与 `BASELINE.md` 留痕。
2. **单变量纪律**：禁止同一批提交同时改 prompt + 判定脚本 + 检索参数。
3. **TDD**：红测先行（watch-it-fail），每个失败路径（超时/无命中/格式坏/熔断/转人工）必须有测试。
4. **审查代理独立性**：控制器不预判 findings、不喂"勿报"指令；每任务独立审查（spec + quality 双判定）。
5. **契约检查必跑**（凡动 schema 的任务）：`generate_openapi.py` + `check_contracts.py`——**这不在 pytest 套件内**，本地全量绿 ≠ 契约绿（CI #57 教训）。
6. **show-your-work 五连问**：声称完成前——来源/可复现/亲眼红/独立期望/限制披露，逐问作答。
7. **实时自我修正**：发现问题即时改，不攒着。

## 五、环境与跑法

### 容器

```bash
# Docker Desktop 必须先启动（今晚崩溃过两次，崩溃后重建容器组）
cd backend && docker compose -f docker-compose.yml --project-directory . up -d postgres redis qdrant
# 等待 healthy（~20s）：docker ps --format "{{.Names}} {{.Status}}" | grep lingxi
```

### Python 与 env 覆盖

- venv：`backend/.venv/Scripts/python.exe`（不要用系统 python，缺依赖）
- 本地跑评测/单测的 env 前缀（Git Bash 写法）：
  ```bash
  POSTGRES_HOST=localhost REDIS_URL=redis://localhost:6379/0 QDRANT_URL=http://localhost:6333 \
  NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 PYTHONIOENCODING=utf-8
  ```

### 常用命令

```bash
# 全量单测
cd backend && <env> ./.venv/Scripts/python.exe -m pytest tests/ -q --no-cov
# ruff
./.venv/Scripts/python.exe -m ruff check app tests alembic scripts
# 迁移对称
POSTGRES_HOST=localhost ./.venv/Scripts/python.exe -m alembic upgrade head
POSTGRES_HOST=localhost ./.venv/Scripts/python.exe -m alembic downgrade -1
POSTGRES_HOST=localhost ./.venv/Scripts/python.exe -m alembic upgrade head
# 契约检查
./.venv/Scripts/python.exe scripts/generate_openapi.py && cd .. && python scripts/check_contracts.py
# 全量评测（~35-50min，LongCat 状态好时）
cd backend && <env> ./.venv/Scripts/python.exe -m scripts.eval_faithfulness \
  --kb-name "星河智家·官方政策库" --out ../eval-and-samples/results/<name>.json
# 存量回填（部署顺序铁律：改 visible filter 前必跑）
QDRANT_URL=http://localhost:6333 NO_PROXY=localhost,127.0.0.1 ./.venv/Scripts/python.exe -m scripts.backfill_visible
# 触发 CI full_eval（用 git credential 取 token）
POST /repos/wjt-code-design/lingxikefu/actions/workflows/ci.yml/dispatches {"ref":"master","inputs":{"full_eval":"true"}}
```

### 提交模式

- 直接 master 提交（本项目既定模式，用户全程知情且验收过）。
- **禁碰 `wt/` 目录**（另一助手在 `wt/backend`、`wt/frontend` worktree 的 `build/backend`、`build/frontend` 分支上工作）。
- push 可能遇网络间歇故障（到 github.com 主站）——后台重试循环（每 90s，最长 45min）恢复即推。

## 六、待办项（优先级排序）

| # | 事项 | 性质 | 前置 | 文档指引 |
|---|---|---|---|---|
| 1 | 门禁 error 率上限加固（防 LongCat 欠费期抽样分母缩水假绿） | 小任务，待用户批准 | 无 | BASELINE.md §四风险登记 |
| 2 | 意图分类切换 / 预起草改道 | 数据依赖 | 影子样本 ≥500 & agree_rate ≥95% 连续两周 | next-execution.md 批次 I |
| 3 | 幻觉域全面扫查（猎手 2/3 串行重派） | 可选 | 无（内联抽查无失实） | — |
| 4 | 四期：选品推荐 / 多渠道 | 外部依赖 | 商品系统接口 / 渠道资质 | next-execution.md 批次 I2 |
| 5 | eval 脚本 latest_kb 兜底收敛 | 判定脚本解冻期 | 判定脚本 hash 冻结期结束 | BASELINE.md §五 |
| 6 | Docker Desktop 稳定性 | 运维 | 无 | 今晚崩溃两次，已恢复 |

## 七、风险与外部依赖

1. **LongCat 账户余额**：今晚 402 欠费致 CI 假红（全 0/0），充值后恢复但有吞吐限速（25s/调用 → 9 字/s，评测时长 35min→3h）。BASELINE.md §四已登记方向性风险（部分 402 缩分母假绿）+ 加固建议（error 率上限）。**余额需监控**。
2. **Docker Desktop 不稳定**：今晚崩溃两次（Wsl2/内存？），崩溃后评测/本地验证全停。崩溃恢复：`Start-Process Docker Desktop` → `docker compose up -d`。
3. **子代理用量限额**：并行代理超 2 个会 `user concurrency limit exceeded`；连续派发遇 `Model request failed`——控制为串行单代理或内联执行。
4. **KB 发布门禁 v2 部署顺序铁律**：检索 filter 加了 `visible=True`，**代码先部署而回填未跑 = 线上检索全挂**（Qdrant 对无 visible 字段的存量 point 不匹配）。`scripts/backfill_visible.py` 必须先于 filter 代码执行 + 对账断言（67/67）。

## 八、关键文件指引

| 文件 | 内容 |
|---|---|
| `docs/superpowers/specs/2026-08-29-closed-loop-architecture.md` | 架构方案 v2.1 + 三期执行回执 + 豁免清单 |
| `docs/superpowers/plans/2026-08-30-next-execution.md` | 后续执行方案（修订 v2）+ H/G/I 回执 + 数据期决策门槛 + 周检查清单 |
| `eval-and-samples/BASELINE.md` | 四件套冻结 + 门禁说明 + 8 节历史记录（污染事件/Q069/LongCat 402/抖动带语义等） |
| `eval-and-samples/BASELINE.sha256` | 评测集三 hash + 判定脚本 hash（9b381e948fb1，G2 授权变更） |
| `eval-and-samples/results/baseline-longcat-20260828-attribution.md` | 逐题归因清单 §六（CI 失败归因 + KB 污染事件全程） |
| `.superpowers/sdd/progress.md` | 全程台账（每任务 complete 行 + 事件登记 + lesson） |
| `.superpowers/sdd/plan-facts*.md` | 各批次现场查证事实清单（file:line 级） |

## 九、常见陷阱与教训（已踩过，别重蹈）

1. **契约检查盲区**：不在 pytest 套件内，本地全量绿仍会契约红——凡动 schema 的任务收尾必跑 `generate_openapi + check_contracts`。
2. **global 作用域陷阱**：嵌套函数写模块级变量必须在赋值所在函数声明 `global`（T5/一期 vector_service ensure_collection 踩过，红测抓获）。
3. **中断代理残留**：子代理被限额/Model failed 中断后可能留下未提交改动——接手时逐文件审查，确认无半成品后提交（H1 踩过）。
4. **评测路径变更须全量实测**：动了检索 filter / refuse 话术 / prompt → 必须跑全量 100 题存档（论证 ≠ 新鲜输出）。
5. **部署顺序**：回填先行 → 再上 filter 代码（否则线上检索全挂）。
6. **抖动带语义**：qa 单跑波动 ±2.5pp 属正常，连续两轮趋势性下滑才触发归因（CI #65/#66 的 0/0 是 LongCat 402 不是代码回归）。
7. **S2 打地鼠教训**：规则式意图词表的泛化靠人补词，结构性不可能覆盖商品词+情绪词混合——影子数据达标后切换 LLM 驱动路由是正道。

## 十、与另一助手的协作约束

- **禁碰** `wt/backend`、`wt/frontend` 两个 worktree 及其分支（`build/backend`、`build/frontend`）——那是另一位助手的工作区。
- 你的工作区是仓库根（master 分支）。另一位助手的工作在独立分支上，互不冲突。
- 如果你需要跑全量评测，先确认 Docker Desktop 健康 + LongCat 账户余额（402 会致全 0/0 假红）。
