# 红队扫查报告：并发 / 共享状态 / 降级路径（2026-08-30）

> **入库注记（2026-08-31）**：本报告原存于 `.superpowers/sdd/bughunt-concurrency.md`（被 `.gitignore` 整体忽略，从未入库）。为保关键审计产物可溯源，复制本副本入库；`docs/` 下本副本为**查阅真源**。
>
> **处置状态（2026-08-31 全量结算）**：**Critical 1/1 修复，Major 8/8 修复，Minor 6/8 修复**。
> - Critical：C1 修复（Redis socket 超时 + chat 热路径线程化）；
> - Major：M1（quick 门禁 fail-closed）、M2（批次清单行锁追加）、M3（评测期事务提前结束）、M4（孤儿批次兜底换会话 + 启动对账）、M5（极性词表补没/未/无/别/莫）、M6（gen 早期异常退款收口）、M7（clarify 回写行锁）、M8（SET NX 原子抢占 + refund Lua token 归属校验）；
> - Minor 已修：m1（线程池关停不排空）、m2（_ensured 404 自愈）、m4（kb_lookup 单飞）、m5（publish/快检同步调用线程化）、m6（「不可以」死条目入词表）、m7（双草稿写前校验）；
> - **挂账（2）→ 2026-09-01 清账**：m3（精确层 get→delete 竞态误删新值——已修：CAS 删除 Lua
>   「值仍等于读取快照才删」，`answer_cache._delete_exact_if_unchanged`，红测先行）；m8（INCR
>   成功后异常已扣费无标记——已修：try_consume 分段异常语义，INCR 后异常保留 marker 供重试
>   幂等放行、INCR 前异常仍释放 marker，双测覆盖）。**Minor 8/8 全部修复**。
> 全部修复 TDD 红测先行（watch-it-fail），红测清单见各提交信息。

范围：quick_answers / intent_shadow / kb_publish_service / answer_cache / quota / ticket_agent+kb_lookup / chat.py 流式段，及其跨模块契约（redis_client、database、kb_publish.py、knowledge.py、eval_faithfulness、ticket_service、vector_service）。
方法：逐条过猎捕清单，每个发现给出「谁/何时/怎么触发」的具体推演链。已验证为**无问题**的高危疑点附在文末（防回归误报）。

发现计数：**Critical 1 / Major 8 / Minor 8**。

---

## Critical

### C1. chat 热路径在事件循环内做同步 Redis 调用，且 redis-py 无 socket 超时 —— Redis 网络黑洞冻结整个进程

- 位置：
  - `backend/app/core/redis_client.py:20`（`redis.from_url(...)` 未设 `socket_timeout`/`socket_connect_timeout`，redis-py 默认 **None=无限阻塞**，connect 走 OS TCP 重试可达 ~2min）
  - `backend/app/api/chat.py:218`（`quota.try_consume` —— **每个 chat 请求**都在 async 端点内直呼同步 Redis INCR/pipeline）
  - `backend/app/api/chat.py:268/271/554`（`quota.refund`，async 生成器内直呼）
  - `backend/app/api/chat.py:370`（`quick_answers.is_enabled_for` → `_covered_version()` → 同步 `get_redis().get`，在 async 生成器 `_events()` 内）
- 触发推演链：
  1. 谁：任意用户发 `/chat/stream`；何时：Redis 主从切换/LB 静默丢包/防火墙黑洞（**连接不断、包不回**——connection-refused 会快速抛错，恰恰是黑洞不会）；怎么触发：请求 A 进入 `chat_stream` → L218 `pipe.execute()` 写出 INCR 后等待响应 → 永不返回。
  2. 事件循环被这一个协程卡死：所有进行中的 SSE 流冻结、`/health` 超时、LB 摘除实例。若是 connect 阶段黑洞，OS TCP 重试让冻结持续分钟级；k8s liveness 杀掉重启后，下一个请求立刻再次冻结。
  3. 讽刺点：answer_cache/quick_answers 的 fail-open、quota 的 fail-closed 全是为「Redis 挂」写的——但**降级代码永远不会执行，因为调用不返回**。单测全绿（mock redis 即时返回/抛错），线上炸法是整服务僵死而非报错。
- 修复：`redis.from_url(..., socket_connect_timeout=1, socket_timeout=1, health_check_interval=15)`；async 上下文中的 4 处调用搬进 `run_in_threadpool`（chat.py 自身 L100 注释声称"同步 DB/阻塞调用统一搬出事件循环"，Redis 是漏网的同一纪律）。

---

## Major

### M1. quick 覆盖门禁在读进程 Redis 失败时静默 fail-open，KB 换血后旧话术放行且无任何告警

- 位置：`backend/app/services/quick_answers.py:147-169`（`_covered_version()` 异常 → `return _COVERED_KB_VERSION`）、`:204-208`（`covered is None → return True`）；模块态 `_COVERED_KB_VERSION`（:111）**只在执行导入链的进程被写**（Celery worker，或 broker 挂掉时的 background-thread 降级路径 `app/api/knowledge.py:241-245`）。
- 触发推演链：
  1. 谁：运营换 KB（删除旧文档/导入新文档）；何时：导入未通过覆盖检查（新版本不写锚点，Redis 里还是旧版本 v1），**随后 Redis 被清空/重启/网络故障**；怎么触发：用户手打「七天无理由退货怎么申请？」→ `match_quick` 命中 → `is_enabled_for(v2)` → `_covered_version()`：Redis 读失败 → API 进程模块态恒为 None（只有 worker 进程写过）→ `covered is None` → **True → 秒回 v1 时代的预置话术**，与刚换血的 KB 直接矛盾（这正是 5-2 门禁要防的「话术陈旧无人知晓」）。
  2. 且观测也失效：`_FALLBACK_NOTED` 分支（:162）要求 `_COVERED_KB_VERSION` 非真才打 info——在 API 进程永远不满足，**静默放行零日志**。docstring 声称「Redis 不可用时回退模块级状态（行为同旧版）」——回退值在读进程恒 None，等同「门禁整体消失」，注释语义与实际不符。
- 修复：读进程 Redis 失败/缺 key 时应 **fail-closed**（禁用 quick 回落 RAG，损失秒回但防陈旧），或把「是否跑过覆盖检查」与「通过的版本」分成两个 key，缺前者即禁用。

### M2. `upsert_batch_membership` 追加路径丢失更新：并发上传同批次 → 文档永远 staged 不可见

- 位置：`backend/app/services/kb_publish_service.py:106-110`（读 `batch.doc_ids` → 内存追加 → `db.commit()`，无 `with_for_update`、无版本校验；:86-96 的 IntegrityError 兜底只保护**首建**路径）。
- 触发推演链：
  1. 谁：管理员对同一 batch_id 并发传 2 个文件（前端多文件上传天然并发）；何时：两请求都通过 `ensure_batch_accepts`（此时批次刚建、status=pending）；怎么触发：A 读 `doc_ids=[d1]`、B 读 `doc_ids=[d1]` → A 提交 `[d1,d2]` → B 提交 `[d1,d3]` → **d2 从批次清单丢失**。
  2. 后果链：d2 的向量带着 `batch_tag=batch_id`、`visible=False` 落在 Qdrant；发布时 `set_visible_by_doc_ids(batch.doc_ids)`（:206-209）按清单翻转——d2 不在清单 → **永远 staged，检索不可见**；`validate_batch_ready` 只校验清单内文档，不报错不通知；批次照样 released。影子 bug：已发布批次回滚后重传同内容还会撞 sha256 去重 400（knowledge.py:196-200）。
- 修复：追加改为 `UPDATE ... SET doc_ids = doc_ids || :x`（PG JSONB 原子合并）或行锁重读（同 chat.py `_update_conv_state_locked` 先例）。

### M3. `_quick_check_job` 跨 ~20min 评测持有一个池连接 + 打开的事务

- 位置：`backend/app/services/kb_publish_service.py:187-199`（`db = SessionLocal()` → `get_batch` 开始事务 → `await _run_quick_check_stage`（~20min LLM 逐题）→ 直到 `_persist_eval_rows` 才 commit）。
- 触发推演链：
  1. 谁：管理员点发布；何时：快检运行期间；怎么触发：首个 SELECT 后连接被 session 持有（SQLAlchemy autobegin），20 分钟内该连接不归还池（pool_size=10 + overflow=20）。
  2. 推演：管理员连发 3 个批次发布 + chat 峰值（每请求 1-2 连接）→ 池耗尽 → `QueuePool limit` 异常 → **全站 chat 500**；PG 侧对应 3 个 idle-in-transaction 长事务，阻碍 vacuum、膨胀 wal。
- 修复：作业开头用短会话取 `batch.doc_ids`/kb 元数据后立即关；评测期间不持有事务（`run_faithfulness_eval` 内部按需短会话）；翻转前重新开短会话再取批次。

### M4. 孤儿 evaluating 批次无任何恢复路径：部署重启/崩溃/兜底失败 → 批次永久砖化

- 位置：`backend/app/services/kb_publish_service.py:220-227`（异常兜底用**同一个可能已坏**的 `db` 会话重查——若是 DB 断连引发的异常，`get_batch` 再抛 PendingRollbackError → 内层 except 只 log → 状态停在 evaluating）；`app/api/kb_publish.py:43-48`（evaluating → publish 409「不可重复发布」）；`app/api/knowledge.py:211-215` + `kb_publish_service.py:66-67/99-105`（evaluating → 上传被拒「请等评测结束后再上传」）。
- 触发推演链：
  1. 谁/何时：快检 ~20min 窗口内发生（a）发版重启（uvicorn kill → asyncio 任务随 loop 消失，无持久化、无 watchdog）；（b）PG 重启（兜底重查失败）；（c）进程 OOM。
  2. 怎么触发：重启后批次 status=evaluating 永久存在——publish 409、上传 400、无通知（`_finish_failed` 没来得及跑）、列表页永远「评测中」。唯一解法是手工改库。任务报告里的「FAILED 也留痕」只覆盖进程存活场景。
- 修复：启动时把所有超时（如 >40min）的 evaluating 批次标 failed+通知；或 eval_task 落 run_id 由启动逻辑对账；兜底重查换新会话。

### M5. answer_cache 极性词表缺「没 / 未 / 无」——否定翻转串答绕过最后一道防线

- 位置：`backend/app/services/answer_cache.py:77-79`（`_POLARITY_TERMS = ("不","不能","无法","不可","不支持","不提供","非","超过","以外","之后")`）、`:96-98`（`_polarity_conflict` 是语义命中前唯一的翻转闸）。
- 触发推演链：
  1. 谁：两个不同用户；何时：KB 里同时存在正/反两种口径的问答；怎么触发：用户 A 问「**没**发货可以退款吗」（答：可以，未发货走仅退款）→ 回填缓存；用户 B 问「发货**了**可以退款吗」（答：不可以，需走质检售后）→ `embed` 余弦 ≥0.85（一字之差）→ 实体锁定：两问均无订单号/型号/商品词 → `_entities_ok` 放行 → 极性：「没」不在词表，两问极性类均为 `frozenset()` → 相等 → **B 拿到 A 的反向答案**。同型：「未激活」vs「已激活」、「无货」vs「有货」。这正是注释声称要挡的「一词之差的翻转」。
  2. 注释还声称「词表只增不减单调加严」，但初始表就漏了最高频的「没/未」。
- 修复：补「没/未/无/别/莫」入表（裸单字噪声只造成良性 miss，符合词表从宽原则）；可选：把「以内/之前」与「超过/之后」成对维护。

### M6. gen() 内层 try 之前的异常：已扣费 + 已落库用户消息，但无 error 事件、无退款

- 位置：`backend/app/api/chat.py:276`（`_fetch_history` 在 try(L346) 之外且无包裹）、`:266`（`request.is_disconnected`）、`:324`（`image_agent.run` 外层——ImageAgent 内部已 fail-open，外层仍裸奔）；配额已在 :218 扣、用户消息已在 :233 落库；退款 finally（:550-555）属于 L346 的 try，**异常点在其之前则 finally 永不进入**。
- 触发推演链：
  1. 谁：DB 抖动（pool 短暂耗尽 / failover）；何时：`_fetch_history` 执行瞬间；怎么触发：`db.scalars` 抛 OperationalError → gen() 在首个 yield 前向 Starlette 抛出 → 连接关闭 → 客户端只见 200+空流（连 `error` 事件都没有）→ `consumed=True` 但退款代码不可达 → **白扣 1 次配额**。
  2. 变体：`_latest_kb_id`（:242，在 gen 之前）抛错 → 端点 500，同样无退款；用户消息已持久化，会话里留一条无回复的消息。带 client_msg_id 的重试可凭幂等标记免重复扣费（部分自愈），**不带 client_msg_id 的调用方每次重试再扣一次**。
- 修复：把 `try:` 上提到 `consumed = True` 之后（或用外层 try/finally 包住整个 gen 体），finally 退款 + `drain_degraded` 才是真正的「单一收口」。

### M7. 澄清回写绕过 P2-⑥ 行锁，整 blob 覆盖并发请求刚写入的 conv_state

- 位置：`backend/app/api/chat.py:462-467`（`s.conv_state = conversation_state.mark_clarifying(s.conv_state)` 后直接 commit——不走 `_update_conv_state_locked` 的 `with_for_update` 重读）。
- 触发推演链：
  1. 谁：同一会话快速连发两条消息（双击/ impatient retry）；何时：第一条触发澄清（`done.clarify=True`），其 RAG 流持续数秒~数十秒；怎么触发：请求 A：helper(L283) 行锁读改写并 commit（窗口开始）→ 流式中；用户此刻发出消息 B → B 的 helper 拿到锁、commit 了含 B 槽位/主题的新状态（窗口内）；A 流到 done → 用 **A 视角快照** mark_clarifying 后整体覆盖 → **B 提取的订单号槽位/主题丢失**。
  2. 后果链：B 消息轮到订单工具分支（:331-332 依赖 `slots[order_no]`）静默不触发，退回 RAG 泛答；`clarify_count` 也基于旧快照，澄清额度多算。单请求内因 SQLAlchemy 身份映射（`locked is s`）恰好正确——单测全绿；只有跨请求窗口才炸。
- 修复：clarify 回写同样走 `_update_conv_state_locked`（重读行 + `conversation_state.mark_clarifying` 合并），或加 `updated_at` 乐观锁拒绝过期写。

### M8. quota 幂等标记 check-then-set 非原子：并发重复请求双扣费；refund 搭车导致免费放行

- 位置：`backend/app/services/quota.py:175-177`（`get(marker)` 命中即放行）与 `:186-187`（消费成功后才 `set(marker)`）两步之间无原子性；`:206-209`（refund 按 marker 存在性 decr+删除，不校验归属）。
- 触发推演链（双扣费）：客户端 35s 超时重发与首请求在飞重叠（R2 本要服务的场景）→ A、B 同 client_msg_id 同时 `get(marker)`=None → 都 INCR → 双双 allowed → 双份流式回答 → **1 个问题扣 2 次**（docstring 声称「同一请求重试不重复扣费」被打破）。
- 触发推演链（免费放行）：A 消费+set marker → B 同 idem 到达，`get(marker)` 命中 → 放行**且不消费** → A 失败走 refund → marker 存在 → decr（退 A 的钱）+删 marker → **B 的回答净扣费 0**。两链每竞态 ±1 次，量级有界但方向都是侵蚀配额正确性。
- 修复：`SET marker NX` 于 INCR 前（拿不到=已扣，直接放行）；refund 改 Lua（校验 marker 值与 user 计数再 decr+del）。

---

## Minor

### m1. 两个非守护线程池无界队列：关停排空上界与「单任务上限」声明不符
`backend/app/services/agents/ticket_agent.py:29-40`、`backend/app/services/intent_shadow.py:159-192`。推演：handoff 突发 50 条 → `_draft_pool(max_workers=2)` 队列 50×(25s LLM+DB)/2 ≈ 10min；注释「单任务上限=25s 不会拖垮关停」只对队首成立，atexit join 等全部排空。影子池同理：sample=0.2 下持续 qa 流量使 meta 落库滞后分钟级（只影响观测时效）。修复：关停钩子 `pool.shutdown(wait=False, cancel_futures=True)` 或减队列+丢弃+计数。

### m2. answer_cache `_ensured` 单进程记忆：集合被外部删除后到重启前缓存永久静默失效
`backend/app/services/answer_cache.py:36-37,44-60`。推演：ops 误删 `answer_cache` 集合 → `_ensured=True` → get/put 的 Qdrant 404 走 fail-open 只打日志 → 命中率归零且无自愈，注释已声明「符合单进程语义」但多 worker 下同样成立。建议：404 时复位 `_ensured` 重试一次。

### m3. 精确层 get→delete RMW 竞态可误删刚回填的新版本值
`backend/app/services/answer_cache.py:113-119` vs `:179-183`。推演：KB 升版瞬间，A `get` 读到旧 payload → `put` 写入 v2 新值 → A `delete(key)` → 新缓存被清 → 下次 miss 多走一次 RAG（自愈，良性）。加版本比较后 `delete` 用 Lua（校验 value 再删）可消。

### m4. `kb_lookup._kb_lock` 锁内做 DB 查询
`backend/app/services/kb_lookup.py:41-52`。推演：DB 停摆 → 每个缓存过期窗口（60s）一个线程持锁做 `db.scalar`，其余 chat 请求在锁上串行排队，最长 `connect_timeout=5s`——有界（H2 已加 connect_timeout）但整段热路径串行冻结 5s。可改为「锁外查、锁内只写缓存」的单飞模式。

### m5. publish 端点与快检协程内的同步阻塞调用违反自家 H2 纪律
`backend/app/api/kb_publish.py:40-64`（async def 内 `get_batch`/`validate_batch_ready` 多次同步查询 + `db.commit` + `audit_log`）；`backend/app/services/kb_publish_service.py:206-216`（async 任务内同步 `set_visible_by_doc_ids`（Qdrant RPC）+ 两次 `db.commit`）。推演：Qdrant/PG 慢 5s → 事件循环冻结 5s，chat SSE 全线停顿。有界（connect_timeout=5）故 Minor；修法同 C1（threadpool）。

### m6. `_POLARITY_CANON` 存在死条目「不可以」
`backend/app/services/answer_cache.py:82`。「不可以」不在 `_POLARITY_TERMS`，永不被采集，canon 映射不可达（现行为靠「不」+「不可」两个子串巧合收敛为 {"不能"}）。词表只增不减的维护约定下，后人按 canon 表扩词会误以为已覆盖。删除该条目并把「不可以」入 terms 更诚实。

### m7. 同一工单并发双草稿：两次 LLM 后 last-write-wins
`backend/app/services/ticket_service.py:119-134`。推演：同会话两条 handoff 消息 → `ensure_active_ticket` 幂等返回同一 ticket → 两次 `_schedule_draft` → 都读到 `draft_suggestion` 为空 → 两次 25s LLM → 后写覆盖先写（浪费一次 LLM，结果仍合法）。读-写间加 `UPDATE tickets SET draft_suggestion=... WHERE draft_suggestion IS NULL` 的条件写即可。

### m8. `try_consume` INCR 成功后异常 → 已扣费但 fail-closed 返回，且无 marker
`backend/app/services/quota.py:179-191`。推演：pipeline 服务端已 INCR、响应丢失（网络闪断）→ except 返回 (False,0) → 用户收 429 但计数已 +1 且无幂等标记 → 重试再 +1。有界漂移，方向是少放行（偏保守），记录在案。

---

## 疑点核实结论（验证过、当前不成立——防后人误改回归）

1. **quota H2 锁外三字段快照**（quota.py:82-94）：写入顺序 value→loaded→cached_at，读者按 loaded→cached_at→value 读；cached_at 新鲜则 value 必不更旧，最坏多进一次锁——与注释声称一致，无正确性问题。
2. **fire-and-forget 捕获请求级对象**：`_schedule_draft`（ticket_agent.py:40）与 `maybe_shadow`（intent_shadow.py:185-188）均只捕获 str/工厂，**未捕获请求级 db/ORM**；两者 worker 内 `SessionLocal()` 短会话——该雷区没有踩。
3. **intent_shadow meta 读改写 vs chat 请求 `user_msg.intent` 写回**（chat.py:414-420）：SQLAlchemy 仅 UPDATE 脏列，两者写不同列，PG 行级 last-write-wins 按列生效，无丢失更新。
4. **早期双重退款疑点**：chat.py:268/271 的 refund 后 `return` 发生在内层 try(L346) **进入之前**，finally 不再触发——无双退（但对照 M6：这些路径之前/同层的异常反而无退款）。
5. **`_publish_tasks`/`_eval_tasks` 集合**：登记 + done_callback discard，仅防 GC，无死登记；batch_tag 写入（vector_service:178-197）与消费（发布按 batch_tag 翻转/回滚重写，:271-279）闭环完整。
6. **`s` 遮蔽链**：chat.py:519 列表推导内 `s` 是推导局部变量（Py3 作用域隔离），不再遮蔽外层 Session——L2 修复有效。
7. **FastAPI 依赖 teardown**：fastapi>=0.111（requirements），`Depends(get_db)` 在流式响应发送完毕后关闭，gen() 内使用 `db` 合法。
8. **快门禁跨进程语义**（Redis 锚点 Celery 写/API 读）：正常路径成立；故障路径见 M1。
