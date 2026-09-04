"""应用配置：单一环境变量真源（Single Source of Truth）。

铁律（规划书红线⑨ / BU-01 spec §2.1）：
- 全部运行时配置只允许从环境变量（或 `.env`）读取，禁止在代码里出现第二份默认值。
- `settings.validate()` 必须在应用启动时调用（见 `app.main`），任何缺 Key / 占位值 / 非法值
  一律抛 `ValueError`，fail-closed，宁可起不来也不带病启动。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Windows 开发机系统代理（WinINET，如 Clash 127.0.0.1:7890）会被 urllib/httpx 经
# getproxies() 拾取（httpx 默认 trust_env=True），即使 shell 无 *_proxy 环境变量。
# 两类故障（均已实测）：
# ① 回环流量被代理拦截 → qdrant-client 超时 → RetrievalError → 检索降级拒答；
# ② 仅设 NO_PROXY（无 *_PROXY env）时 getproxies_environment() 返回空 → urllib
#    回退注册表代理——代理客户端已关但 ProxyEnable 仍开时，外网（LongCat）请求
#    拾取"死代理" → ConnectError 10061（2026-09-02 eval 全量 ERR 根因）。
# 修复：进程内显式设置 NO_PROXY 豁免「回环地址 + 外网 AI 服务域名直连」。NO_PROXY
# 存在且无代理变量 → env 判定生效、注册表代理整体不再拾取（httpx 按 no_proxy
# 匹配决定 bypass）；LongCat/火山 ARK 均为国内可直连服务（trust_env=False 直连
# 200 实测）。若部署环境显式给了 HTTP(S)_PROXY，直连语义同样成立（NO_PROXY 优先
# 于代理变量）；CI/Linux 无注册表代理机制，本设置无副作用。
_LOOPBACK_NO_PROXY = "localhost,127.0.0.1,::1"
# 外网 AI 服务域名从 env 或默认值提取（此时 Settings 尚未实例化，BASE_URL 可被 env 覆盖）：
# - LongCat：对话/评测主链路
# - 火山 ARK：图片理解（ImageAgent 视觉模型）
_EXTERNAL_HOSTS = [
    urlparse(os.environ.get(u, d)).hostname or urlparse(d).hostname or ""
    for u, d in (
        ("LONGCAT_BASE_URL", "https://api.longcat.chat/openai"),
        ("VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
    )
]
for _np_key in ("NO_PROXY", "no_proxy"):
    _np_cur = os.environ.get(_np_key, "")
    _missing = [d for d in (_LOOPBACK_NO_PROXY, *_EXTERNAL_HOSTS) if d and d not in _np_cur]
    if _missing:
        os.environ[_np_key] = ",".join([_np_cur, *_missing]) if _np_cur else ",".join(_missing)
del _np_key, _np_cur, _LOOPBACK_NO_PROXY, _EXTERNAL_HOSTS, _missing

#: 上传图片目录（P4：默认绝对路径——相对路径随进程 CWD 漂移，容器/服务化下会写到
#: 不可预期目录；以本文件（backend/app/core/）为锚点定位到 backend/uploads/images）
_IMG_DIR = Path(__file__).resolve().parents[2] / "uploads" / "images"

#: JWT_SECRET 的占位值；validate() 遇到该值即拒绝启动,
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
    ENV: Literal["dev", "test", "prod"] = "dev"

    # --- 安全（M1：登录/注册防爆破限流；测试/内部环境可关闭，prod 必须开启） ---
    RATE_LIMIT_ENABLED: bool = True

    # --- 配额（BU-08：每用户每日问答上限，Redis 计数） ---
    DAILY_QUOTA_LIMIT: int = 200

    # --- 匿名会话（D1 完整特性，2026-09-04）：免登录体验，防滥用三闸 ---
    # 每 IP 每日可发放的 guest 主体数（超发 429）
    GUEST_ISSUE_PER_IP_PER_DAY: int = 3
    # guest 每日问答上限（远低于注册用户，控 LLM 成本）
    GUEST_DAILY_QUOTA_LIMIT: int = 10
    # guest 数据留存天数：超期由调度器删 user 行（会话/反馈/画像 FK CASCADE 级联）；0=关闭清理
    GUEST_RETENTION_DAYS: int = 30

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

    # --- 工单自动化（2026-08-22）：时间阈值配置，<=0 表示关闭对应自动化 ---
    # 客服回复后 N 分钟无用户新消息 → 自动 resolved（0=关闭）
    AUTO_TICKET_RESOLVE_TIMEOUT_MIN: int = 30
    # 工单空闲 N 天 → 自动 closed（0=关闭）
    AUTO_TICKET_CLOSE_IDLE_DAYS: int = 7

    # --- 意图影子（架构二期 3，ADR-1 第一步：LLM 意图分类只记不驱动） ---
    # 采样率 [0,1]：对规则判为 qa 的用户消息按此概率打 LLM 影子分类（结果只落
    # Message.meta["intent_shadow"]，不驱动路由）；0 = 关闭影子（不产生 LLM 成本）。
    # 2026-09-04 由 0.2 提至 1.0：线上 qa 流量实测仅 ~2-10 条/天，20% 采样下攒够
    # INTENT_SHADOW_MIN_TOTAL=500 需 ~250 天（切换门槛实际不可达）；影子走关思考
    # 短调用（~1-2s、约 400 token/次），全量采样月成本量级可忽略。
    INTENT_SHADOW_SAMPLE: float = 1.0
    #: 意图影子切换决策的最小样本量（H4 观测）：stats.remaining<=0 即样本量达标，
    #: 是否切换仍需结合 agree_rate 与人工评审，本字段只做进度观测。
    INTENT_SHADOW_MIN_TOTAL: int = 500

    # --- 租户（MVP 单租户，Phase3 才启用行级过滤） ---
    TENANT_DEFAULT: str = "default"

    # --- 模型（2026-08-27 全面收敛：对话/评测只用 LongCat；embedding 本地 bge） ---
    # chat provider：仅 longcat（已全面取消百炼/智谱）
    CHAT_PROVIDER: str = "longcat"
    # LongCat API（OpenAI 兼容端点）；CHAT_PROVIDER=longcat 时生效。Key 只经 env 注入，严禁提交。
    LONGCAT_API_KEY: str | None = None
    LONGCAT_BASE_URL: str = "https://api.longcat.chat/openai"
    LONGCAT_CHAT_MODEL: str = "LongCat-2.0"
    # LongCat-2.0 为推理模型：默认开思考（思维链经 SSE reasoning 事件透传，前端展示
    # "思考中"——用户感知首反馈 ~2s；实测思维链对拒答/防编造判定有实质贡献：
    # 关思考时拒答锚点统计通过率仅 83%，开思考 100%）。置 false 可关（省 token 换速度）。
    LLM_ENABLE_THINKING: bool = True
    # 火山引擎（视觉模型）：Image Agent 图片理解
    VOLCENGINE_API_KEY: str | None = None
    VOLCENGINE_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/v3"
    VOLCENGINE_CHAT_MODEL: str = "ep-m-20260811130634-mnpgq"
    # B1（安全）：聊天图片上传白名单目录——ImageAgent 仅允许读取该目录内的图片。
    # 客户端传来的 image_paths 是服务器路径，不做白名单校验可读任意文件（经视觉模型外泄）。
    IMAGE_UPLOAD_DIR: str = str(_IMG_DIR)  # P4：绝对路径（防 CWD 漂移）
    # embedding：local=本机 BAAI/bge-base-zh-v1.5（0 成本、不出境；已取消百炼 embedding）
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

        - JWT_SECRET 必须为真实值，禁止占位符；
        - PostgreSQL / Redis / Qdrant 必填项不得为空；
        - POSTGRES_PORT 必须为合法端口。
        """
        errors: list[str] = []

        if not self.JWT_SECRET or self.JWT_SECRET == PLACEHOLDER_SECRET:
            errors.append(
                "JWT_SECRET 未配置或仍为占位值 __CHANGE_ME__（请生成随机密钥并注入环境变量）"
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

        # --- 意图影子采样率（架构二期 3）---
        if not (0 <= self.INTENT_SHADOW_SAMPLE <= 1):
            errors.append(
                f"INTENT_SHADOW_SAMPLE 非法值: {self.INTENT_SHADOW_SAMPLE!r}（应为 0~1 的采样率，0=关闭）"
            )

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
