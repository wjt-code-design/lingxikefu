# ---------- 多阶段构建：Lingxi API 镜像 ----------

# ---- Stage 1: 依赖安装 ----
FROM python:3.13-slim AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt .
# CPU-only torch：本系统 embedding 走本地模型（EMBEDDING_PROVIDER=local，bge）在 CPU 推理，
# 不需要 CUDA。两段式：先用官方 CPU index 无版本装 torch 到 /install，再借 PYTHONPATH（指向
# 真正的 site-packages 子目录）让随后的 requirements 复用已满足的 torch —— 避免
# sentence-transformers 间接依赖默认 torch 连带下载整套 CUDA 库（可达 1-2GB）。
# 单 RUN 保持层结构与之前一致。
RUN PYTHONPATH=/install/lib/python3.13/site-packages sh -c "\
    pip install --prefix=/install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install --prefix=/install --no-cache-dir -r requirements.txt"
# pytest-asyncio 单独装（2026-08-20）：
# - 不写入 requirements.txt（改动它会破坏上一条 RUN 的层缓存 → 全量重下依赖，国内网络 30min+）
# - 内联阿里云镜像源秒装；独立 RUN 层，requirements 层缓存命中时本层也命中
RUN pip install --prefix=/install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ pytest-asyncio

# ---- Stage 2: 运行时 ----
FROM python:3.13-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# 非 root 运行
RUN useradd --create-home --uid 10001 appuser

# 从构建阶段复制依赖
COPY --from=builder /install /usr/local

# 复制应用与迁移
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
# .env.example 不再 COPY 进运行镜像（D9，审计 2026-09-27）：app/core/config.py 读的是环境变量，
# 运行期用不到这个模板；CI 的 secret-scan 读的是工作区文件，与镜像无关。
# pytest 配置（asyncio_mode=auto 等；2026-08-20 补：容器内跑测试需要）
COPY pyproject.toml .
# 测试与工具脚本进镜像（2026-08-20：容器内可直接 pytest / 重新生成 OpenAPI schema，
# 不再需要 docker cp 临时拷入）
COPY tests ./tests
COPY scripts ./scripts

USER appuser

EXPOSE 8000

# fail-closed：启动前由 settings.validate() 校验配置（缺/占位即退出）
#
# 并发形态：**本镜像按单 worker 前提运行**（下方 CMD 故意不带 --workers），原因不是"不敢加"，
# 而是进程内确实有共享可变状态——通知中心是 SSE 长连接（app/api/notifications.py
# /notifications/stream），订阅表 app/services/notification_service.py 的 `_subscribers`
# 是模块级 dict + threading.Lock，广播只 loop.call_soon_threadsafe 投递给**本进程**的连接。
# 因此加 --workers N 后，约 (N-1)/N 的在线坐席收不到实时推送，而列表和角标仍由 DB 兜底 ⇒
# 静默降级、无人报错（B6，审计 2026-09-27）。
# 要横向扩容必须先做的一步：把通知订阅表挪到 Redis pub/sub（跨进程广播），
# 另两处同源进程内状态（app/api/sessions.py 的 _suggest_cache、app/api/telemetry.py 的
# 降级 _recent）只影响命中率/单进程防护，可随后评估。
# 其余跨实例可变状态早已外部化：限流=Redis、答案缓存=Redis+Qdrant、导入任务=Celery(broker=Redis)、
# 业务数据=PostgreSQL。代理后面如需真实客户端 IP，须配 TRUSTED_PROXIES（填法见仓库 backend/.env.example）。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
