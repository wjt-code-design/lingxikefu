"""用户画像服务（长期记忆，2026-08-22，Phase B：规则聚合采集）。

跨会话用户画像（long-term memory）：
- **规则聚合**：复用 `session_context.FLOW_TOPICS`（主题词表）+ `query_rewrite._extract_entities`
  （实体提取）——单一真源，不重复定义词表（PL#13）；
- 纯函数 `extract_signals` + 薄 DB `merge_profile`（乐观锁 + 幂等键）：
  - 乐观锁：`version` 条件更新，防多 worker 并发丢更新；
  - 幂等键：同一 `message_id` 只计一次（消息落库重复触发不翻倍）；
- **个人上下文绝不进入 answer_cache**：本服务只写 `user_profiles`，不触碰缓存；
  采集挂点均在消息落库后调用（chat/feedback），与缓存回填路径分离；
- fail-open：任何异常降级为"跳过本次采集"，不阻断回答（PL#8）；
- 配置开关 `settings.USER_PROFILE_ENABLED`：关闭 = 不采集（挂点判开关）。
"""
from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session as OrmSession

from app.core.config import settings
from app.core.redis_client import get_redis
from app.models.user_profile import UserProfile
from app.services.query_rewrite import _extract_entities
from app.services.session_context import FLOW_TOPICS, _is_generic

logger = logging.getLogger(__name__)

#: 画像 JSONB schema 版本（向前兼容：新版本字段追加，不改旧字段语义）
_SCHEMA_VERSION = 1
#: 实体去重上限（防画像无限膨胀，超限丢弃最旧）
_ENTITY_LIMIT = 20
#: 品类偏好词表（新增，收敛在本服务；触达即计入偏好品类）
_CATEGORY_TERMS = (
    "手机", "平板", "笔记本", "电脑", "冰箱", "洗衣机", "空调", "电视",
    "耳机", "充电器", "空气净化器", "扫地机器人", "显示器",
)
#: 转人工信号阈值：转人工次数达此值标记"高优服务用户"（Prompt 引导用）
_HANDOFF_HIGH_PRIORITY = 2


def _is_category(query: str) -> list[str]:
    """从问句中识别品类偏好（命中 _CATEGORY_TERMS 的商品词）。"""
    return [t for t in _CATEGORY_TERMS if t in query]


def extract_signals(query: str, intent: str, role: str = "user") -> dict[str, Any]:
    """从单条消息抽取画像增量信号（纯函数，可测）。

    - topics: 主题 -> +1（复用 FLOW_TOPICS 词表，命中即累加）
    - entities: 订单号/型号（复用 _extract_entities，去重）
    - categories: 偏好品类（_CATEGORY_TERMS）
    - handoff: intent == 'handoff' 时 +1（转人工信号）
    - satisfaction: 由 feedback 单独传入（本函数不含，见 merge_profile 的 sat_rating 参数）
    """
    signals: dict[str, Any] = {
        "topics": {},
        "entities": [],
        "categories": [],
        "handoff": 0,
    }
    if not query:
        return signals

    for name, keys in FLOW_TOPICS:
        if any(k in query for k in keys):
            signals["topics"][name] = 1

    entities = _extract_entities(query)
    # 只保留具体标识实体（订单号/型号），去掉泛化商品词（与 session_context 一致防噪声）
    signals["entities"] = [e for e in entities if not _is_generic(e)]

    signals["categories"] = _is_category(query)

    if intent == "handoff":
        signals["handoff"] = 1
    return signals


def _merge_one(profile: dict[str, Any], signals: dict[str, Any]) -> dict[str, Any]:
    """合并 signals 进 profile，**返回新 dict**（JSON 列原地突变不触发 SQLAlchemy 变更检测，
    必须整体赋值才写库——测试亲眼红过此陷阱）。"""
    merged = dict(profile)
    if signals.get("topics"):
        topics = dict(merged.get("topics") or {})
        for name, cnt in signals["topics"].items():
            topics[name] = topics.get(name, 0) + cnt
        merged["topics"] = topics

    if signals.get("entities"):
        entities = list(merged.get("entities") or [])
        for e in signals["entities"]:
            if e not in entities:
                entities.append(e)
        merged["entities"] = entities[:_ENTITY_LIMIT]

    if signals.get("categories"):
        cats = list((merged.setdefault("preferences", {})).get("品类") or [])
        for c in signals["categories"]:
            if c not in cats:
                cats.append(c)
        merged["preferences"]["品类"] = cats

    if signals.get("handoff"):
        h = dict(merged.get("handoff") or {})
        h["count"] = h.get("count", 0) + signals["handoff"]
        merged["handoff"] = h

    if signals.get("satisfaction"):
        sat = dict(merged.get("satisfaction") or {})
        for k, v in signals["satisfaction"].items():
            if k in ("up", "down"):
                sat[k] = sat.get(k, 0) + v
        merged["satisfaction"] = sat
    return merged


def merge_profile(
    db: OrmSession,
    user_id: Any,
    query: str,
    intent: str = "qa",
    *,
    idem_key: str | None = None,
    sat_rating: str | None = None,
) -> bool:
    """增量合并画像（乐观锁 + 幂等键）。返回是否写入成功。

    - idem_key：幂等键（如 message_id / feedback 的 message_id），同一键重复调用不重复计数；
      用 user_profiles 表外一张轻量记忆不可行（不引新表），改用"签名缓存"：
      本服务用 Redis 记录已处理幂等键（fail-open：Redis 异常则放行，宁可重复不丢信号）。
    - sat_rating：feedback 满意度（up/down），单独累加。
    - fail-open：任何异常 log 后返回 False，不抛（不阻断回答）。
    """
    if not settings.USER_PROFILE_ENABLED:
        return False
    try:
        signals = extract_signals(query, intent)
        if sat_rating in ("up", "down") and sat_rating:
            signals["satisfaction"] = {sat_rating: 1}

        if idem_key and _already_processed(idem_key):
            return True  # 幂等：已处理过（避免重复计数）

        row = db.scalar(
            sa.select(UserProfile).where(
                UserProfile.tenant_id == settings.TENANT_DEFAULT,
                UserProfile.user_id == user_id,
            )
        )
        if row is None:
            row = UserProfile(
                tenant_id=settings.TENANT_DEFAULT,
                user_id=user_id,
                profile={"schema_version": _SCHEMA_VERSION},
            )
            db.add(row)
            db.flush()

        # M3（外部审查 2026-08-22）：真乐观锁——UPDATE 带 WHERE version 条件 + 失败重读重试。
        # 此前是纯读-改-写（注释声称防并发丢更新，实无 WHERE 条件）：同一用户连续快速提问时
        # 后提交者会覆盖先提交者的画像增量（主题次数/实体漏记）。
        for _attempt in range(3):
            result = db.execute(
                sa.update(UserProfile)
                .where(UserProfile.id == row.id, UserProfile.version == row.version)
                .values(
                    profile=_merge_one(row.profile, signals),
                    version=row.version + 1,
                )
                .execution_options(synchronize_session=False)
            )
            db.commit()
            if result.rowcount:
                if idem_key:
                    _mark_processed(idem_key)
                return True
            # CAS 未命中 = 并发覆盖：回滚、重读最新行再试（保留对方增量后合并本次信号）
            db.rollback()
            row = db.scalar(
                sa.select(UserProfile).where(
                    UserProfile.tenant_id == settings.TENANT_DEFAULT,
                    UserProfile.user_id == user_id,
                )
            )
            if row is None:
                return False
        logger.warning("画像合并连续 CAS 冲突，放弃本信号（fail-open 不阻断回答）: user=%s", user_id)
        return False
    except Exception:  # noqa: BLE001 - fail-open
        logger.exception("用户画像采集失败（跳过，不阻断回答）")
        db.rollback()
        return False


def _already_processed(idem_key: str) -> bool:
    """Redis 幂等键查重（fail-open：Redis 异常返回 False 放行）。"""
    try:
        return get_redis().exists(_idem_key_name(idem_key)) == 1
    except Exception:  # noqa: BLE001
        return False


def _mark_processed(idem_key: str) -> None:
    """标记幂等键（fail-open：失败不影响主流程）。"""
    try:
        get_redis().set(_idem_key_name(idem_key), "1", ex=60 * 60 * 24)  # 24h 内同键不重复计
    except Exception:  # noqa: BLE001
        pass


def _idem_key_name(idem_key: str) -> str:
    return f"user_profile_idem:{idem_key}"


def get_profile(db: OrmSession, user_id: Any) -> dict[str, Any] | None:
    """读取画像（原始 JSONB）；无画像返回 None。"""
    row = db.scalar(
        sa.select(UserProfile).where(
            UserProfile.tenant_id == settings.TENANT_DEFAULT,
            UserProfile.user_id == user_id,
        )
    )
    return dict(row.profile) if row else None


def to_prompt_text(profile: dict[str, Any] | None) -> str | None:
    """画像 → prompt 注入文本（聚合摘要，限量；无内容返回 None）。

    输出如：用户画像：常问主题 退款(5)/物流(3)；历史关联实体 SO2026080118、W5；
    满意度 赞3/踩1；曾转人工2次（高优）；偏好品类 洗衣机。
    """
    if not profile or not isinstance(profile, dict):
        return None
    parts: list[str] = []
    topics = profile.get("topics") or {}
    if topics:
        t = "/".join(f"{k}({v})" for k, v in sorted(topics.items(), key=lambda x: -x[1]))
        parts.append(f"常问主题 {t}")
    entities = profile.get("entities") or []
    if entities:
        parts.append(f"历史关联实体 {'、'.join(entities[:8])}")
    sat = profile.get("satisfaction") or {}
    if sat:
        parts.append(f"满意度 赞{sat.get('up', 0)}/踩{sat.get('down', 0)}")
    handoff = profile.get("handoff") or {}
    if handoff.get("count"):
        hc = int(handoff["count"])
        parts.append(f"曾转人工{hc}次" + ("（高优服务）" if hc >= _HANDOFF_HIGH_PRIORITY else ""))
    prefs = profile.get("preferences") or {}
    cats = prefs.get("品类") or []
    if cats:
        parts.append(f"偏好品类 {'、'.join(cats[:6])}")
    return "用户画像：" + "；".join(parts) if parts else None


def reset_profile(db: OrmSession, user_id: Any) -> bool:
    """清空画像（隐私控制）。返回是否删除。"""
    row = db.scalar(
        sa.select(UserProfile).where(
            UserProfile.tenant_id == settings.TENANT_DEFAULT,
            UserProfile.user_id == user_id,
        )
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
