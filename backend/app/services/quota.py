"""配额服务（BU-08）：每用户每日问答次数，Redis 计数器。

设计要点：
- 计数键按日期隔离，TTL 48h 自动清理。
- ``try_consume``：原子闸门 —— INCR 后比对上限，超额回滚并拒绝（修复 M2 TOCTOU 竞态）。
- R2 幂等/回滚：``try_consume`` 支持 ``idem_key``（客户端提问幂等键 client_msg_id），
  同一请求重试（断连/超时后重发）命中幂等标记 → 不重复扣费；
  调用方在失败路径（断连/知识库为空/系统异常）调 ``refund`` 回滚已扣配额，解决断连白扣。
- Redis 不可用时 ``try_consume`` **fail-closed 拒绝**（而非放行），保证配额保护不失效；
  ``left_today``/``used_today`` 仅供 /quota 展示，Redis 不可达时优雅返回 0/满额（不 5xx）；
  ``refund`` fail-open（回滚失败不阻塞主流程，重试重新扣费兜底）。
- 上限动态化（架构一期 6）：``daily_limit()`` 优先读 ``app_settings`` KV 覆盖
  （admin PUT /admin/settings/quota 写入），60s 进程内 TTL 缓存，KV 读失败回退
  settings 常量而非拒绝服务。
- redis 客户端 / DB session 工厂可注入，便于单测用内存假对象替换（无需真起 Redis/PG）。
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redis_client import get_redis
from app.models.app_setting import AppSetting

logger = logging.getLogger(__name__)

#: R2：幂等标记 TTL（与配额计数 key 一致，48h 自动清理）
_IDEM_TTL_SECONDS = 60 * 60 * 48

#: M8 收尾：refund 原子脚本——「GET 校验 marker 归属（token）→ DECRBY → DEL」单命令原子执行。
#: 旧三步（GET 判存在 → DECR → DEL）非原子：GET 与 DEL 之间 marker 可被同指纹重试请求
#: 重新抢占（换主），refund 会退掉新请求的费并删掉新锁；且幂等命中放行的并发请求 B
#: （未扣费）退款时会误退持锁者 A 的配额。KEYS[1]=marker KEYS[2]=counter
#: ARGV[1]=expected_token ARGV[2]=n
_REFUND_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('DECRBY', KEYS[2], ARGV[2])
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

#: 架构一期 6：每日上限的 app_settings KV 键（覆盖 settings.DAILY_QUOTA_LIMIT）
DAILY_LIMIT_KV_KEY = "quota.daily_limit"

#: daily_limit() 的 KV 覆盖进程内缓存 TTL（秒）：生效延迟上界，同时把 KV 读
#: 频率压到每进程每 60s 一次（含「无覆盖」负缓存），避免热路径每请求查库
_LIMIT_TTL_SECONDS = 60


def _today() -> str:
    return date.today().isoformat()


class QuotaService:
    def __init__(self, redis_client=None, session_factory=None) -> None:
        self._redis = redis_client
        #: app_settings KV 读用的 session 工厂（None → 惰性取 SessionLocal）；
        #: 测试注入内存 SQLite 工厂，使 KV 读与 PUT 写通道落在同一库
        self.session_factory = session_factory
        #: daily_limit() 进程内缓存态：_limit_loaded=False 表示未加载；
        #: _limit_value=None 表示「KV 无覆盖」负缓存（同样受 TTL 约束）
        self._limit_lock = threading.Lock()
        self._limit_loaded = False
        self._limit_value: int | None = None
        self._limit_cached_at = 0.0

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    def _db_session(self):
        factory = SessionLocal if self.session_factory is None else self.session_factory
        return factory()

    def daily_limit(self) -> int:
        """每日配额上限：app_settings KV 覆盖优先（60s 进程内 TTL 缓存），回退 settings。

        - **双检**（H2 债清偿）：锁外无锁读快照（GIL 下单字段读原子；三字段撕裂
          最坏=多进一次锁，无正确性影响）——命中零锁零等待，DB 停摆时不堵热路径；
        - miss 进锁 double-check（等锁期间他人可能已回填）再读库发布（单飞防击穿）；
        - KV 读失败（DB 不可达）回退 settings 常量而非拒绝服务（fail-open 方向），
          失败结果同样按 TTL 负缓存，避免对故障 DB 每请求重试（恢复延迟 ≤ TTL）。
        """
        if (
            self._limit_loaded
            and time.monotonic() - self._limit_cached_at < _LIMIT_TTL_SECONDS
        ):
            return self._limit_value if self._limit_value is not None else settings.DAILY_QUOTA_LIMIT
        with self._limit_lock:
            if self._limit_loaded and time.monotonic() - self._limit_cached_at < _LIMIT_TTL_SECONDS:
                return self._limit_value if self._limit_value is not None else settings.DAILY_QUOTA_LIMIT
            value = self._read_daily_limit_kv()
            self._limit_value = value
            self._limit_loaded = True
            self._limit_cached_at = time.monotonic()
            return value if value is not None else settings.DAILY_QUOTA_LIMIT

    def _read_daily_limit_kv(self) -> int | None:
        """读 KV 覆盖值；无行 / 非法值 / DB 失败一律返回 None（回退 settings）。"""
        try:
            db = self._db_session()
            try:
                row = db.get(AppSetting, DAILY_LIMIT_KV_KEY)
            finally:
                db.close()
            if row is None or row.tenant_id != settings.TENANT_DEFAULT:
                return None
            value = row.value
            # JSON 标量校验：bool 是 int 子类需排除；非法值视为无覆盖（不信任库内脏数据）
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                logger.warning("app_settings[%s] 非法值 %r，忽略 KV 覆盖", DAILY_LIMIT_KV_KEY, value)
                return None
            return value
        except Exception:  # noqa: BLE001 - KV 读失败回退 settings（fail-open，不拒绝服务）
            logger.warning("app_settings KV 读取失败，daily_limit 回退 settings", exc_info=True)
            return None

    def set_daily_limit(self, db: Session, value: int) -> None:
        """写 KV 覆盖（upsert）并失效进程内缓存 —— admin 写通道（PUT /admin/settings/quota）。

        tenant_id 走 tenant_id_column() 的列默认值（settings.TENANT_DEFAULT，与全仓写路径
        一致）；并发 PUT 同时判「无行」各插入的 PK 冲突：回滚后重读改更新，幂等收敛到同一值。
        """
        row = db.get(AppSetting, DAILY_LIMIT_KV_KEY)
        if row is None:
            db.add(AppSetting(key=DAILY_LIMIT_KV_KEY, value=value))
        else:
            row.value = value
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.get(AppSetting, DAILY_LIMIT_KV_KEY)
            if row is not None:
                row.value = value
                db.commit()
        self.invalidate_limit_cache()

    def invalidate_limit_cache(self) -> None:
        """清 daily_limit 生效缓存（PUT 写通道调用）——秒级生效，不等 60s TTL。"""
        with self._limit_lock:
            self._limit_loaded = False
            self._limit_value = None

    def _key(self, user_id: str, day: str | None = None) -> str:
        return f"quota:{user_id}:{day or _today()}"

    def _idem_key(self, idem_key: str) -> str:
        return f"quota:idem:{idem_key}"

    def used_today(self, user_id: str, day: str | None = None) -> int:
        try:
            return int(self.redis.get(self._key(user_id, day)) or 0)
        except Exception:  # Redis 不可达 → 视为 0 已用（仅展示用）
            return 0

    def left_today(self, user_id: str) -> int:
        return max(0, self.daily_limit() - self.used_today(user_id))

    def try_consume(self, user_id: str, n: int = 1, idem_key: str | None = None, content: str | None = None, token: str | None = None) -> tuple[bool, int]:
        """原子扣减闸门（P1-①）：幂等抢占 SET NX 原子化 + INCR/expire MULTI pipeline。

        R2 幂等指纹：marker 绑定 ``sha256(user_id:content)``（``content=None`` 退化为
        ``sha256(user_id:idem_key)`` 兼容旧调用）——跨用户复用 idem_key / 同用户换
        content 均不再共享幂等标记，杜绝裸 marker 免费放行。

        M8（bughunt-concurrency）：幂等标记由「先 GET 后 SET」改为 ``SET NX EX``
        单命令原子抢占——旧两步法在并发窗口内 N 个同指纹请求都看到 marker 不存在
        而重复扣费。抢不到 marker = 已有并发请求持锁扣费中，本次幂等命中不重复扣。

        M8 收尾：``token`` 为请求级归属凭证（chat 层生成 uuid4，与 refund 成对传递）。
        抢占成功时写入 marker 值；``refund`` 按值校验归属后才回滚——防止幂等命中的
        并发请求（未扣费）误退持锁者的配额。不传 token 时退化为旧值 ``"1"``（兼容）。

        返回 (allowed, used)：见模块 docstring。
        Redis / pipeline 不可用时 fail-closed 返回 (False, 0)（不产生半步脏状态）。

        m8（bughunt-concurrency）分段异常语义：
        - INCR 之前的异常（未扣费）：释放已抢占的 marker，重试正常扣费；
        - INCR 之后的异常（扣费生效性未知，如 execute 响应丢失）：**保留 marker**——
          重试同指纹命中幂等标记放行，消除「已扣费但无标记 → 重试双扣」；
          若 INCR 实际未生效则赠送一次放行（服务用户方向，优于双扣）。
        """
        marker: str | None = None
        acquired = False
        pipe = None
        try:
            r = self.redis
            if idem_key:
                material = content if content else idem_key
                fingerprint = hashlib.sha256(f"{user_id}:{material}".encode()).hexdigest()
                marker = self._idem_key(fingerprint)
                # M8：SET NX 原子抢占（SET 成功返回 True；key 已存在返回 None）
                acquired = bool(r.set(marker, token or "1", ex=_IDEM_TTL_SECONDS, nx=True))
                if not acquired:
                    # 幂等命中：同用户同内容此前已扣过费（重试/并发请求），本次不重复扣
                    return True, self.used_today(user_id)
            # pipeline 构造属 INCR 前步骤（code-review 修正：redis-py 构造纯内存不触网，
            # 失败仅测试 fake 可达；放本段保证「INCR 前异常必释放 marker」语义无例外）
            pipe = r.pipeline()
        except Exception:  # noqa: BLE001 - INCR 前异常：未扣费，释放标记后 fail-closed
            logger.warning("quota try_consume: INCR 前异常（未扣费），fail-closed 拒绝", exc_info=True)
            if acquired and marker:
                try:
                    self.redis.delete(marker)
                except Exception:  # noqa: BLE001 - 清理失败仅告警
                    logger.warning("quota try_consume: 异常路径清理幂等标记失败", exc_info=True)
            return False, 0

        try:
            key = self._key(user_id)
            pipe.incr(key, n)
            pipe.expire(key, 60 * 60 * 48)
            used = int(pipe.execute()[0])
            if used > self.daily_limit():
                r.decr(key, n)  # 超额回滚，避免占用配额
                if acquired and marker:
                    r.delete(marker)  # 抢占未成立（超额拒绝）→ 释放标记，重试可重新正常扣费
                return False, used - n
            return True, used
        except Exception:  # noqa: BLE001 - INCR 后异常：扣费生效性未知 → 保留 marker（m8）
            logger.warning(
                "quota try_consume: INCR 后异常（扣费生效性未知），保留幂等标记供重试幂等放行",
                exc_info=True,
            )
            return False, 0

    def refund(self, user_id: str, n: int = 1, idem_key: str | None = None, content: str | None = None, token: str | None = None) -> None:
        """失败回滚（P1-①）：按与 try_consume 相同的指纹定位标记，校验归属后原子回滚。

        回滚入参（含 content、token）与扣费入参一致才命中同一枚标记；``token`` 与
        扣费时传入的请求级凭证必须匹配（Lua 原子 GET 比较 → DECRBY → DEL），
        防止幂等命中放行的并发请求退款时误退持锁者的配额、或换主窗口退错费。
        marker 不存在 / 值不匹配 = 本次未持有扣费，无动作。
        Redis 不可达时 fail-open（不阻塞主流程）。
        """
        try:
            r = self.redis
            if idem_key:
                material = content if content else idem_key
                fingerprint = hashlib.sha256(f"{user_id}:{material}".encode()).hexdigest()
                marker = self._idem_key(fingerprint)
                # M8 收尾：Lua 原子「归属校验 → DECRBY → DEL」（token 不匹配无动作）
                r.eval(_REFUND_LUA, 2, marker, self._key(user_id), token or "1", n)
            else:
                r.decr(self._key(user_id), n)
        except Exception:  # noqa: BLE001 - fail-open：回滚失败不阻塞
            logger.warning("quota refund: redis 不可用，跳过回滚", exc_info=True)


_quota_service: QuotaService | None = None


def get_quota_service(redis_client=None) -> QuotaService:
    """业务路径使用模块级单例（复用 Redis 连接，避免每次请求重建实例）；

    测试可显式传入 redis_client 获取隔离的新实例。
    """
    global _quota_service
    if redis_client is not None:
        return QuotaService(redis_client=redis_client)
    if _quota_service is None:
        _quota_service = QuotaService()
    return _quota_service
