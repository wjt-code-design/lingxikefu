"""应用配置：单一环境变量真源（Single Source of Truth）。

铁律（规划书红线⑨ / BU-01 spec §2.1）：
- 全部运行时配置只允许从环境变量（或 `.env`）读取，禁止在代码里出现第二份默认值。
- `settings.validate()` 必须在应用启动时调用（见 `app.main`），任何缺 Key / 占位值 / 非法值
  一律抛 `ValueError`，fail-closed，宁可起不来也不带病启动。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: JWT_SECRET / LITELLM_MASTER_KEY 的占位值；validate() 遇到该值即拒绝启动，
#: 防止 aegisdesk-ai 那种「默认值三处自相矛盾 / 占位密钥泄漏」的坑。
PLACEHOLDER_SECRET = "__CHANGE_ME__"


class Settings(BaseSettings):
    """Lingxi 后端配置。

    未配置的字段保持空字符串 / 默认值，由 `validate()` 统一把关；
    这样 pydantic 实例化不会因缺 Key 直接崩，缺 Key 的判定集中在一处（fail-closed 语义清晰）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- PostgreSQL（关系库） ---
    POSTGRES_HOST: str = ""
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""

    # --- Redis（缓存 / 会话上下文 / 配额计数 / Celery broker） ---
    REDIS_URL: str = ""

    # --- Qdrant（向量库） ---
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str | None = None

    # --- JWT ---
    JWT_SECRET: str = PLACEHOLDER_SECRET
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- 配额（BU-08：每用户每日问答上限，Redis 计数） ---
    DAILY_QUOTA_LIMIT: int = 200

    # --- 租户（MVP 单租户，Phase3 才启用行级过滤） ---
    TENANT_DEFAULT: str = "default"

    # --- LiteLLM 网关（百炼 Key 由网关 env/KMS 注入，禁止直写真实值） ---
    LITELLM_MASTER_KEY: str = PLACEHOLDER_SECRET

    # --- 模型（ADR-3：百炼 + LiteLLM；embedding 本地 bge 优先，可切百炼） ---
    # chat 主模型：客服高频问答用 flash 档（快 + 便宜），max 档仅降级/复杂任务
    CHAT_MODEL: str = "qwen3.7-flash-2026-07-15"
    CHAT_MODEL_FALLBACK: str | None = "deepseek-v4-flash-0731"
    # 百炼 API Key（env 注入，勿提交；缺失时 llm client 运行时报清晰错误，不做启动强校验，
    # 以便无 Key 环境下单测/CI 可跑、本地 embedding 方案不依赖它）
    DASHSCOPE_API_KEY: str | None = None
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # embedding：local=本机 BAAI/bge-base-zh-v1.5（0 成本、不出境）；bailian=百炼 text-embedding
    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_MODEL: str = "BAAI/bge-base-zh-v1.5"
    # rerank：MVP 关闭（管线预留节点，评测 recall@5 不达标再开）
    RAG_ENABLE_RERANK: bool = False
    RERANK_MODEL: str = "gte-rerank-v2"

    # --- CORS ---
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        description="JSON 数组格式，如 [\"http://localhost:5173\"]",
    )

    @property
    def database_url(self) -> str:
        """SQLAlchemy 连接串（psycopg3 驱动）。"""
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    def validate(self) -> None:
        """启动校验（fail-closed）：任一检查失败即抛 ValueError。

        - JWT_SECRET / LITELLM_MASTER_KEY 必须为真实值，禁止占位符；
        - PostgreSQL / Redis / Qdrant 必填项不得为空；
        - POSTGRES_PORT 必须为合法端口。
        """
        errors: list[str] = []

        if not self.JWT_SECRET or self.JWT_SECRET == PLACEHOLDER_SECRET:
            errors.append(
                "JWT_SECRET 未配置或仍为占位值 __CHANGE_ME__（请生成随机密钥并注入环境变量）"
            )
        if not self.LITELLM_MASTER_KEY or self.LITELLM_MASTER_KEY == PLACEHOLDER_SECRET:
            errors.append(
                "LITELLM_MASTER_KEY 未配置或仍为占位值 __CHANGE_ME__（请由 LiteLLM 网关 secret 注入）"
            )

        for name, value in {
            "POSTGRES_HOST": self.POSTGRES_HOST,
            "POSTGRES_USER": self.POSTGRES_USER,
            "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD,
            "POSTGRES_DB": self.POSTGRES_DB,
            "REDIS_URL": self.REDIS_URL,
            "QDRANT_URL": self.QDRANT_URL,
        }.items():
            if not value:
                errors.append(f"{name} 缺失（未设置环境变量或 .env 未提供）")

        if not (1 <= self.POSTGRES_PORT <= 65535):
            errors.append(f"POSTGRES_PORT 非法值: {self.POSTGRES_PORT!r}（应为 1-65535 的整数）")

        if errors:
            raise ValueError(
                "Lingxi 配置启动校验失败（fail-closed），拒绝启动：\n"
                + "\n".join(f"  - {e}" for e in errors)
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例：保证全应用共享同一份配置。"""
    return Settings()


#: 模块级单例，模型 / 迁移 / 中间件直接引用。
settings = get_settings()
