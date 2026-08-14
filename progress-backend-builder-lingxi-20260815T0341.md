# BU-01 后端脚手架 进度文件

> trace_id: `lingxi-20260815T0341`
> 单元: BU-01 项目脚手架（W1）
> Worktree: `wt/backend`（分支 `build/backend`）
> 状态: 已交付待验收（本地验证结果见下）

---

## 1. 做了什么

按《后端规划》§8 BU-01 与 dispatch/BU-01.md §1 范围，只交付**可运行脚手架**，不实现任何业务功能（auth/session/knowledge/rag 等在 BU-02~BU-09）。

### 1.1 app/ 应用骨架
| 路径 | 说明 |
|---|---|
| `app/main.py` | FastAPI 入口：启动即 `settings.validate()`（fail-closed）、`/health`、CORS、请求 ID 中间件、挂载 6 组 api router（前缀 `/api/v1`） |
| `app/core/config.py` | Pydantic Settings 单一 env 真源 + `validate()`（JWT_SECRET/LITELLM_MASTER_KEY 占位即拒、必填缺失即拒、端口非法即拒） |
| `app/core/database.py` | SQLAlchemy 2.0 engine + sessionmaker + `get_db()` 依赖 |
| `app/core/security.py` | JWT 签发/解析占位（python-jose，BU-02 完善） |
| `app/api/` | auth / sessions / chat / knowledge / feedback / quota / admin 各 APIRouter 占位（空路由，后续单元填充） |
| `app/models/` | **11 张表全部含 `tenant_id`**（第一个非 id 列，建索引，MVP 默认 `default`）：users / sessions / messages / message_sources / knowledge_bases / documents / chunks / chunk_context(预留) / feedback / quotas / tickets(预留)；quotas 含唯一约束 `(tenant_id,user_id,date)` |
| `app/repositories|services|llm_clients|retrieval|rag_pipeline|orchestrator|prompts/` | 分层目录占位 |
| `app/workers/celery_app.py` | Celery app（broker/backend=REDIS_URL），BU-04 注册导入任务 |

### 1.2 基础设施
- `alembic/`：env.py（URL 从 config 单一真源注入）+ script.py.mako + `versions/0001_initial.py`（首版迁移：11 表 + 全部 tenant_id 索引 + `uq_quotas_tenant_user_date`，upgrade/downgrade 双写，downgrade 显式回收 5 个 PG enum）。
- `requirements.txt` + `pyproject.toml`：全依赖用兼容区间；ruff 规则（E/F/I/W/UP）、pytest 配置（pythonpath="."）。
- `.env.example`：**全部占位**（`__CHANGE_ME__`），无任何真实密钥/endpoint。
- `Dockerfile`：多阶段构建（builder 装依赖 → runtime 非 root 跑 uvicorn）。
- `docker-compose.yml`：postgres(16) + redis(7) + qdrant(v1.9.1) + migrate(oneshot) + api + worker(celery)，全部带 healthcheck；敏感项用 `${VAR:-default}` 保证 config 可校验，启动 fail-closed。
- `.github/workflows/ci.yml`：ruff → pytest → secret 扫描（gitleaks 等价 Python 扫描）→ 构建镜像 + `docker compose config` 校验。

### 1.3 tests/
- `tests/conftest.py`：注入测试环境变量（保证 import app.main 时 validate() 通过）。
- `tests/test_config.py`：**占位 JWT_SECRET / LITELLM_MASTER_KEY、缺必填、非法/非数字端口 → validate() 抛错**；默认值自洽；env 全缺失 fail-closed；database_url 拼接。
- `tests/test_health.py`：TestClient 验 `/health` 返回 `{"status":"ok","tenant":"default"}` + X-Request-ID。
- `tests/test_models_tenant.py`：11 张表全部含 `tenant_id` 列（且为第一个非主键列、建索引）+ quotas 唯一约束 + 表集合完整性。

---

## 2. 关键文件清单

```
backend/
├── app/
│   ├── main.py  core/{config,database,security}.py
│   ├── api/{auth,sessions,chat,knowledge,feedback,quota,admin}.py
│   ├── models/{base,user,session,message,knowledge,feedback,quota,ticket}.py
│   ├── repositories|services|llm_clients|retrieval|rag_pipeline|orchestrator|prompts/__init__.py
│   └── workers/{__init__,celery_app}.py
├── alembic/{env.py, script.py.mako, versions/0001_initial.py}
├── tests/{conftest,test_config,test_health,test_models_tenant}.py
├── .env.example  requirements.txt  pyproject.toml
├── Dockerfile  docker-compose.yml  .dockerignore
└── .github/workflows/ci.yml
```

---

## 3. 如何本地验证

```bash
# 依赖装到隔离 venv（禁止污染全局）
<venv>/python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

# 单测（TestClient，不连真库）
<venv>/python -m pytest

# lint
<venv>/python -m ruff check app tests alembic

# 启动 + /health（需先设置有效环境变量，否则 fail-closed 拒绝启动）
set JWT_SECRET=xxx ... && <venv>/python -m uvicorn app.main:app --port 8000
curl http://localhost:8000/health
```

---

## 4. 本地验证结果（2026-08-15 实跑）

| 项 | 结果 |
|---|---|
| 依赖安装（隔离 venv，阿里云镜像） | ✅ 全部成功（fastapi 0.141 / sqlalchemy 2.0.52 / alembic 1.19 / litellm 1.96 / qdrant-client 1.19 / celery 5.6 / pytest 8.4 / ruff 0.16） |
| `pytest tests/` | ✅ **23 passed**（config 16 + health 2 + models 5） |
| `GET /health`（uvicorn 实启 :8011） | ✅ HTTP 200 `{"status":"ok","tenant":"default"}` |
| fail-closed 启动校验 | ✅ 缺 JWT_SECRET/LITELLM_MASTER_KEY 时 `import app.main` 抛 ValueError 拒绝启动 |
| `ruff check app tests alembic` | ✅ All checks passed |
| `python -m compileall app alembic tests` | ✅ SYNTAX_OK |
| `docker compose config --quiet` | ✅ COMPOSE_VALID_OK（本机有 compose CLI v5.3.1） |
| `alembic upgrade head --sql`（离线） | ✅ EXIT=0，37 条 CREATE（11 表 + 26 索引/约束），含 `uq_quotas_tenant_user_date` |
| `alembic downgrade 0001:base --sql`（离线） | ✅ EXIT=0，16 条 DROP（11 表 + 5 enum type 回收） |

> 注意：首次 pip 安装曾因沙箱回收站不可用（SAFE_DELETE_FAIL_CLOSED）中途失败，加 `--no-cache-dir` 后成功；`alembic.ini` 中文注释在 Windows GBK locale 下读取报 UnicodeDecodeError，已改为英文注释。pytest 用 `-p no:cacheprovider` 规避沙箱写缓存警告。

## 5. 需 docker 环境验证（本地无 docker daemon）

- `docker compose up`（api+postgres+redis+qdrant+worker 真实拉起）——需 docker daemon。
- `alembic upgrade head` 在真 PostgreSQL 16 上执行（env.py 已接 config 单一真源，迁移 SQL 语法正确）。
- Dockerfile 多阶段构建（`docker build`）。

## 6. 遗留项（非本单元范围）

- `app/api/*` 为占位空 router，业务端点由 BU-02~BU-09 填充。
- `core/security.py` 为占位实现，真实签发/校验 BU-02 完成。
- CI 中 mypy/pyright 类型检查放宽为仅 ruff（DoD 允许「可放宽」）。
- gitleaks 未作为独立二进制引入，CI 用等价 Python 扫描（DoD 允许「或等价扫描」）。

---

## 7. DoD 勾选表（BU-01.md §3）

- [x] `wt/backend/backend/` 结构完整，可 `uvicorn app.main:app` 启动（验证见 §4）
- [x] `GET /health` 返回 `{"status":"ok",...}`；`pytest tests/test_health.py` 通过（§4）
- [x] `pytest tests/test_config.py` 通过：缺 Key / 占位 Key / 非法值 → `validate()` 抛错；默认值无矛盾（§4）
- [x] `pytest tests/test_models_tenant.py` 通过：所有模型含 `tenant_id`（§4）
- [x] `alembic` 迁移存在且 `upgrade()/downgrade()` 双写；真 PG 执行需 docker 环境（§5 标注）
- [x] `docker-compose.yml` 通过 `docker compose config` 校验（✅ 本地实跑）
- [x] `ruff` lint 通过（§4）
- [x] 无真实密钥/endpoint 提交（`.env.example` 全占位，CI secret 扫描兜底）
- [x] `git` 已在 `build/backend` 分支提交，commit message 含 trace_id（见 §8）

## 8. 提交

- commit: 见执行日志（`git add backend && git commit -m "feat(backend): BU-01 scaffold [trace_id=lingxi-20260815T0341]"`，未 push）
