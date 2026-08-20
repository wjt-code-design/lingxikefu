# ---------- 多阶段构建：Lingxi API 镜像 ----------

# ---- Stage 1: 依赖安装 ----
FROM python:3.13-slim AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt
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
COPY .env.example .
# pytest 配置（asyncio_mode=auto 等；2026-08-20 补：容器内跑测试需要）
COPY pyproject.toml .
# 测试与工具脚本进镜像（2026-08-20：容器内可直接 pytest / 重新生成 OpenAPI schema，
# 不再需要 docker cp 临时拷入）
COPY tests ./tests
COPY scripts ./scripts

USER appuser

EXPOSE 8000

# fail-closed：启动前由 settings.validate() 校验配置（缺/占位即退出）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
