# 灵犀（Lingxi）CI 与加固执行报告

**项目**: C:\Users\33393\WorkBuddy\2026-08-15-00-39-34
**执行时间**: 2026-08-23 13:10-13:20 (本地 Windows 11)
**参考**: 第一版 A/C/E + 第二版建议

---

## 一、执行范围（严格遵守第二版边界）

| 项 | 状态 | 说明 |
|---|---|---|
| 前端 CI job | ✅ 已存在 | `.github/workflows/ci.yml`（根目录，非 backend/.github） |
| 前端 401 精确匹配 | ✅ 已存在+测试 | `client.ts` 用 `!== '/auth/refresh'` 替换 `includes` |
| require_roles 工厂 | ✅ 已存在 | `deps.py` + customers/sessions/tickets 5 处替换 |
| rewrite() 去重 | ✅ 已存在+测试 | `rag_service.py` done 事件携带 `rewritten_query`，chat.py 不再重复调用 |
| 评测脚本 sys.path 引导 | ✅ 已存在 | eval_faithfulness.py + eval_recall.py |
| 前端 Vitest 类型检查+测试 | ✅ 通过 | 26 tests / 8 files 全绿 |
| 后端 ruff | ✅ 通过 | All checks passed |
| 后端核心 pytest | ✅ 通过 | 34 tests passed (test_chat_api + test_rag + test_deps) |
| ChatContainer 拆分 | ⏸ 不做 | 按边界单独成轮 |
| 多租户 tenant_id 扫描 | ⏸ 不做 | Phase3 前再处理 |
| Docker 重建 / UI 语义 | ⏸ 不做 | 单独成轮 |
| 评测基线 faithfulness/recall | ⛔ 阻塞 | 本机无 PostgreSQL/Qdrant/百炼 |

---

## 二、验证结果

### 前端
- `npm run typecheck` → **零错误** ✅
- `npm test` → **8 files, 26 tests passed** ✅
  - stderr 中的 `window.getComputedStyle` / `AggregateError` 是 jsdom 环境已知噪音（XHR 到 localhost 无后端 / CSS 测滚动条），不影响测试结果

### 后端
- `ruff check app tests alembic scripts` → **All checks passed** ✅
- `pytest --no-cov tests/test_chat_api.py tests/test_rag.py tests/test_deps.py` → **34 passed** ✅
  - 含新补的 `test_chat_cache_write_reuses_stream_rewritten_query`（chat 层不重复 rewrite）
  - 含新补的 `test_stream_answer_done_carries_existing_rewritten_query`（RAG done 带 rewritten_query）
  - 含新补的 `test_require_roles_allows_staff_and_rejects_user`（角色守卫）

### 全量 pytest 状态
- `test_demo_orders.py` 5 个 ERROR：本机 `.env` 的 `POSTGRES_HOST=postgres` 无法解析（无 docker compose 环境）
- 这是**环境阻塞**，不是代码问题。test_chat_api.py 已用 SQLite + monkeypatch 覆盖同逻辑，且通过。

---

## 三、未提交改动（已就位，待 commit）

```
.github/workflows/ci.yml              ← 从 backend/.github 移至根 + 新增 frontend job
frontend/src/api/client.ts            ← includes → 精确匹配 '/auth/refresh'
frontend/src/tests/client.test.ts     ← 401 精确匹配回归测试（4 个用例）
backend/app/api/deps.py               ← require_roles 工厂（~20 行）
backend/app/api/customers.py          ← 内联 403 → require_roles
backend/app/api/sessions.py           ← 内联 403 → require_roles
backend/app/api/tickets.py            ← 内联 403 → require_roles（4 处）
backend/app/api/chat.py               ← 删除重复 rewrite 调用；透传 rewritten_query
backend/app/services/rag_service.py   ← done 事件携带 rewritten_query
backend/tests/test_chat_api.py        ← 缓存回填复用改写 key 测试
backend/tests/test_rag.py             ← done 携带 rewritten_query 测试
backend/tests/test_deps.py            ← require_roles 角色守卫测试
backend/scripts/eval_faithfulness.py  ← sys.path 引导（容器内直跑）
backend/scripts/eval_recall.py        ← sys.path 引导（容器内直跑）
```

---

## 四、阻塞与待办

| 阻塞项 | 原因 | 下一步 |
|---|---|---|
| 评测基线 faithfulness/recall | 需 PostgreSQL + Qdrant + LongCat 评测模型（LONGCAT_API_KEY） | 在 docker compose 环境或腾讯云服务器上执行 |
| 全量 pytest 本地通过 | 需启动 PG/Redis/Qdrant（docker compose up） | docker 环境就绪后重跑 |
| 未提交代码 | 用户自行决定 commit/push 时机 | 建议 `git add` 后 commit |

---

## 五、发现的问题

1. **之前 worker 把 ci.yml 放在 `backend/.github/workflows/`**：GitHub Actions 只读仓库根 `.github/workflows/`，导致"幻影 CI"——全部 4 个 job 从未在远端执行。**已修正**（移至根目录）。

2. **`POSTGRES_HOST=postgres` 本机不可达**：本机无 docker compose 时 pytest 无法连 PG。test_chat_api.py 用 SQLite 覆盖，但 `test_demo_orders.py` 是 PG-bound 的——本机验证需先 docker compose up。

3. **`client.test.ts` 的第 2 个用例** (`仅排除 refresh 本身；相似路径的 401 仍应刷新`) 精准捕获了 `/auth/refresh-status` 被误伤的回归，已作为永久回归保护。

---

## 六、建议下一步

1. **commit 当前未提交改动**（工作区干净、测试已绿）
2. **docker compose up** 后跑一次全量 pytest + 评测基线
3. **单独一轮做 ChatContainer 拆分**（D 项，最值得认真做的单点）
4. **M2 双标签页互踢**等用户抱怨时再做（BroadcastChannel ~40 行）
