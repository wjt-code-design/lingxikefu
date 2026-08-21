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

#: 已知不安全的开发默认密钥（M5）：生产环境（ENV=prod）严禁使用，避免 token 被伪造。
KNOWN_WEAK_SECRETS = {
    "9f2a7c4e1b8d6a3f5c0e7b2d4a6f8c1e",  # 历史开发默认值
}

#: 生产环境 JWT_SECRET 最小长度（>=32 位强随机）。
PROD_SECRET_MIN_LEN = 32


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

    # --- 运行环境（M5：dev / prod；prod 强制强密钥） ---
    ENV: str = "dev"

    # --- 安全（M1：登录/注册防爆破限流；测试/内部环境可关闭，prod 必须开启） ---
    RATE_LIMIT_ENABLED: bool = True

    # --- 配额（BU-08：每用户每日问答上限，Redis 计数） ---
    DAILY_QUOTA_LIMIT: int = 200

    # --- 答案缓存（T10：省 token + 提速；可一键降级） ---
    ANSWER_CACHE_ENABLED: bool = True
    # 语义命中余弦阈值：实测 0.95 过高——同义改写句（"如何申请七天无理由退货" vs
    # "七天无理由退货怎么申请"）余弦仅 0.94，被拒导致语义层形同虚设（只有一字不差命中）。
    # 0.85：同义改写（0.84-0.94）命中、不同主题（0.43）拒绝，区分清晰；
    # 串答风险由实体锁定（_entities_ok）+ kb_id/kb_version 校验兜底，阈值偏激进无副作用（miss 仅回落 LLM）。
    ANSWER_CACHE_THRESHOLD: float = 0.85
    # 2026-08-21：TTL 24→72h，提高常见问题命中窗口（miss 仅回落 LLM，无副作用）。
    ANSWER_CACHE_TTL_HOURS: int = 72

    # --- 用户画像（长期记忆，2026-08-22 Phase B）：可一键关闭（关闭=不采集不注入，回答不变） ---
    USER_PROFILE_ENABLED: bool = True

    # --- 租户（MVP 单租户，Phase3 才启用行级过滤） ---
    TENANT_DEFAULT: str = "default"

    # --- LiteLLM 网关（百炼 Key 由网关 env/KMS 注入，禁止直写真实值） ---
    LITELLM_MASTER_KEY: str = PLACEHOLDER_SECRET

    # --- 模型（ADR-3：百炼 + LiteLLM；embedding 本地 bge 优先，可切百炼） ---
    # chat provider：bailian（默认）/ zhipu（智谱 GLM，百炼额度耗尽时的备选）
    CHAT_PROVIDER: str = "bailian"
    # chat 主模型：客服高频问答用 flash 档（快 + 便宜），max 档仅降级/复杂任务
    CHAT_MODEL: str = "qwen3.7-flash-2026-07-15"
    CHAT_MODEL_FALLBACK: str | None = "deepseek-v4-flash-0731"
    # 百炼 API Key（env 注入，勿提交；缺失时 llm client 运行时报清晰错误，不做启动强校验，
    # 以便无 Key 环境下单测/CI 可跑、本地 embedding 方案不依赖它）
    DASHSCOPE_API_KEY: str | None = None
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # 智谱（GLM）API：OpenAI 兼容端点；CHAT_PROVIDER=zhipu 时生效
    ZHIPU_API_KEY: str | None = None
    ZHIPU_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    ZHIPU_CHAT_MODEL: str = "glm-5.1"
    # embedding：local=本机 BAAI/bge-base-zh-v1.5（0 成本、不出境）；bailian=百炼 text-embedding
    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_MODEL: str = "BAAI/bge-base-zh-v1.5"
    # rerank：MVP 关闭（管线预留节点，评测 recall@5 不达标再开）
    RAG_ENABLE_RERANK: bool = False
    RERANK_MODEL: str = "gte-rerank-v2"
    # hybrid 检索（ADR-2026-08-16）：sparse(BM25 bigram) + dense + RRF。
    # true 用 QDRANT_COLLECTION_HYBRID（named vectors dense+sparse，需重建索引）；
    # false 回退纯 dense 旧集合（QDRANT_COLLECTION，无需重导，回滚路径）。
    RAG_ENABLE_HYBRID: bool = True

    # --- 知识库导入（BU-04：分块 / 上传限制 / 向量集合） ---
    # 分块参数：中文按字符计；块内尽量保留段落结构，单段超长再硬切
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 50
    # 检索 top_k：2026-08-21 降噪，8→5。参数扫描（10 条业务查询真检索）显示 top5 相对 top8
    # precision@5 20%→32%（纯收益），recall@5 不变（95%）；top8 多出的低分近义文档只稀释上下文。
    RETRIEVAL_TOP_K: int = 5
    # 检索拒答阈值（L4：top-1 分数低于此值视为无可靠依据 → 拒答；原硬编码 0.30 提为配置）
    MIN_SCORE: float = 0.30
    # 上传文件大小上限（MB），超出拒绝
    MAX_UPLOAD_MB: int = 10
    # Qdrant 集合名：带维度后缀（bge=768 / text-embedding-v3=1024），
    # 防换 embedding provider 后误写旧集合（维度不同 = 语义空间不同，必须重建）
    QDRANT_COLLECTION: str = "lingxi_bge_768"
    # hybrid 专用集合：named vectors（dense+sparse），与纯 dense 旧集合物理隔离（回滚只需切开关）
    QDRANT_COLLECTION_HYBRID: str = "lingxi_hybrid_bge_768"

    # --- CORS ---
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        description="JSON 数组格式，如 [\"http://localhost:5173\"]",
    )
    #: 可信反向代理直连 IP 白名单（安全）：只有在 request.client.host（TCP 对端）属于此列表时，
    #: 才信任 X-Forwarded-For 首段作为客户端 IP（登录/注册限流 key）。默认空 → 恒用 TCP 对端、
    #: 忽略 XFF（fail-closed，防未受保护时客户端伪造 IP 绕过限流）。
    TRUSTED_PROXIES: list[str] = Field(
        default_factory=list,
        description="JSON 数组格式，如 [\"10.0.0.1\"]；空则忽略 X-Forwarded-For",
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

        # M5：生产环境强制强密钥 —— 禁止开发默认密钥、要求 >=32 位随机值
        if self.ENV == "prod":
            if self.JWT_SECRET in KNOWN_WEAK_SECRETS:
                errors.append(
                    "生产环境 ENV=prod 禁止使用开发默认 JWT_SECRET，必须通过密钥管理注入随机值"
                )
            elif len(self.JWT_SECRET) < PROD_SECRET_MIN_LEN:
                errors.append(
                    f"生产环境 ENV=prod 的 JWT_SECRET 长度不足（{len(self.JWT_SECRET)} < {PROD_SECRET_MIN_LEN}），必须使用强随机密钥"
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

        # --- BU-04 知识库导入参数 ---
        if self.CHUNK_SIZE <= 0:
            errors.append(f"CHUNK_SIZE 非法值: {self.CHUNK_SIZE!r}（应 > 0）")
        if not (0 <= self.CHUNK_OVERLAP < self.CHUNK_SIZE):
            errors.append(
                f"CHUNK_OVERLAP 非法值: {self.CHUNK_OVERLAP!r}（应满足 0 <= overlap < CHUNK_SIZE={self.CHUNK_SIZE}）"
            )
        if not (1 <= self.MAX_UPLOAD_MB <= 100):
            errors.append(f"MAX_UPLOAD_MB 非法值: {self.MAX_UPLOAD_MB!r}（应为 1-100 MB）")
        if not self.QDRANT_COLLECTION.strip():
            errors.append("QDRANT_COLLECTION 缺失（向量集合名不能为空）")

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
