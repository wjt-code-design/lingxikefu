"""快捷问题预置话术短路（方案A：彻底杜绝"点快捷要思考"）。

无论前端点快捷按钮还是输入框手动输入相同问句，只要命中本表，后端直接返回预置答案，
秒回、零思考、不检索。kb_version 失效面（架构审核债 5-2）：知识导入成功后跑覆盖检查
（check_kb_coverage），通过则记录通过版本——Redis 锚点（quick:covered_kb_version，跨进程：
Celery worker 写、API/chat 进程读，架构三期 2）+ 模块级 _COVERED_KB_VERSION 兜底；
chat 端短路前用 is_enabled_for(kb_version) 校验——KB 已变更而新版本未通过覆盖检查时禁用
quick 回落 RAG，防"KB 更新后话术陈旧无人知晓"。从未跑过覆盖检查的环境恒放行（向后兼容，
行为同旧版）。Redis 不可用时回退模块级状态（行为同旧版），不依赖 Redis 存活。

- 与 KB/后端真实回答对齐；若改知识库相关章节，需同步更新此处答案。
- 匹配用归一化句（TFKC + 去标点/空白），手打"一模一样"或近似标点差异均可命中。
"""
from __future__ import annotations

import logging
import re
import unicodedata

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

#: 快捷问题 -> 预置答案（markdown，简短、直接给结论）
_QA: list[tuple[str, str]] = [
    (
        "七天无理由退货怎么申请？",
        "## 七天无理由退货申请\n- 我的订单 → 申请售后 → 七天无理由退货\n- 客服审核后寄回商品，填写物流单号\n- 质检通过后 1-3 个工作日原路退款",
    ),
    ("退款一般多久到账？", "质检通过后 1-3 个工作日原路退回；信用卡最长 7 个工作日。超 7 个工作日未到账可联系客服核查。"),
    ("支持哪些支付方式？", "本平台支持微信支付、支付宝、银联云闪付三种主流支付方式。"),
    ("可以开发票吗？", "支持开具电子普通发票与增值税专用发票。个人抬头仅电子普票，企业可开专票（需提供开票信息）。"),
    ("保修多久？", "手机、平板、笔记本等数码产品为 12 个月整机保修；具体品类以商品保修条款为准。"),
    ("如何修改收货地址？", "订单未发货（已付款未出库）可在订单详情修改地址；已发货需联系承运快递拦截退回/转寄新地址。"),
    ("换货要怎么做？", "商品未激活、未拆封或质量问题可申请换货：我的订单 → 申请售后 → 换货 → 寄回旧品，质检通过后发出新品（质量问题免运费）。"),
    ("退货进度在哪看？", "在「我的订单-售后」可查看退货/换货进度，跟踪审核、寄回、质检、退款各节点。"),
    ("物流单号在哪查？", "在「我的订单-物流详情」可实时查看物流单号与配送轨迹。"),
    ("下单后多久发货？", "现货数码商品 48 小时内发货；大家电为预约配送；预售商品以详情页标注的发货时间为准。"),
    ("预售要等多久？", "预售商品发货时间以商品详情页标注为准，到货后按订单顺序发出。"),
    ("屏幕有坏点保修吗？", "屏幕坏点/亮点在保修范围内（详见商品保修条款，OLED 微弱亮度不均为正常）。"),
    ("电池健康度低于80%保修吗？", "电池容量自然衰减属于正常损耗，不在保修范围（详见商品保修条款第五节）。"),
    ("手机配置参数在哪看？", "数码商品配置（处理器/内存/屏幕等）在商品详情页「参数」区查看，具体以实物及官网规格为准。"),
    ("系统怎么升级？", "设置 → 关于本机 → 系统更新 → 检查更新，选择在线升级（OTA）；重大版本星河会通过站内信通知。"),
    ("怎么修改登录密码？", "路径：个人中心 → 设置 → 账号与安全 → 修改密码。需验证当前密码后设新密码（字母+数字、≥8 位）；忘记密码可走「找回密码」。"),
    ("如何绑定或解绑手机号？", "账号与安全 → 手机号 → 更换，验证原手机号后用新号换绑；换号/换机前请先绑定新常用手机号，避免无法登录。"),
    ("短信验证码收不到怎么办？", "先查：是否被拦截（骚扰拦截）、信号、号码。多次收不到可改用语音验证码或邮箱验证码；仍收不到联系人工核实身份——客服不会索要验证码。"),
    ("价保怎么申请？退差价多久到账？", "订单详情 →「价保」→ 系统自动比对当前售价，差额原路退回（1-3 个工作日）。7 天价保，秒杀/百亿补贴/券后到手价不参与。"),
    ("优惠券能叠加使用吗？", "不可叠加。单笔订单限用 1 张满减券，且不可与其他平台满减活动同享。"),
    ("手机可以以旧换新吗？", "支持，不限品牌与购买渠道。结算页「以旧换新」入口可在线估价，或预约上门检测取件。"),
    ("以旧换新怎么估价？", "估价依据机型、容量、外观成色、功能完好度与能否开机；最终抵扣额以回收商检测为准。"),
]

#: 归一化（TFKC 全角→半角、去标点/空白、小写）——用于匹配手打输入
_PUNCT_RE = re.compile(r"[\s，。？！；：、“”‘’\"'（）()【】\[\],.!?;:]")


def _norm(s: str) -> str:
    return _PUNCT_RE.sub("", unicodedata.normalize("NFKC", s).lower())


#: 归一化问句 -> 预置答案（模块加载时建表）
_QUICK: dict[str, str] = {_norm(q): a for q, a in _QA}


def match_quick(content: str) -> str | None:
    """命中快捷预置话术则返回预置答案，否则 None（走正常 RAG）。"""
    if not content:
        return None
    return _QUICK.get(_norm(content))


#: 高频虚词（去停用后取 bigram，避免"的/吗/怎么"泛词误判覆盖）
_STOP_WORDS = [
    "怎么", "如何", "可以", "哪里", "哪儿", "有没有", "什么", "多久", "在哪", "请问",
    "吗", "呢", "吧", "的", "在", "是", "了", "要", "会", "能", "我", "我们", "您", "需",
]


def _topic_bigrams(q: str) -> set[str]:
    """问题去虚词后取字符 bigram（覆盖判据的核心词元）。"""
    norm = "".join(ch for ch in _norm(q) if ch not in _STOP_WORDS and not ch.isspace())
    return {norm[i : i + 2] for i in range(len(norm) - 1)}


def uncovered_questions(kb_text: str) -> list[str]:
    """P4：快捷话术 vs KB 双源漂移启发式——返回在 KB 中无覆盖依据的问题列表。

    对每个快捷问题：去虚词取 bigram，KB 文本（归一化后）若不含其中任一 bigram，
    判定该问题在 KB 中无覆盖依据。调用方（knowledge_import_service）对列表记 warning，
    提示"该话题只有话术没有文档"，运营据此补录。
    """
    kb_norm = _norm(kb_text or "")
    uncovered: list[str] = []
    for q, _a in _QA:
        grams = _topic_bigrams(q)
        if grams and not any(g in kb_norm for g in grams):
            uncovered.append(q)
    return uncovered


#: 覆盖门禁阈值（架构审核债 5-2）：有 KB 依据的话术占比 ≥ 该值才算"覆盖检查通过"。
#: 守卫 KB 换血/大面积删除（过半话术失据 → quick 禁用走 RAG）；局部漂移（个别话题
#: 未覆盖）仍由 uncovered_questions 的 warning 告警，不至于一票否决整表话术。
_COVERAGE_PASS_RATIO: float = 0.5

#: 最近一次覆盖检查通过的 kb_version（模块级状态；None = 从未通过/从未检查）。
#: 由 knowledge_import_service 导入成功后调用 check_kb_coverage 写入；
#: chat.py quick 短路前经 is_enabled_for 比对消费。
#: 架构三期 2：模块级是进程内状态（Celery worker 写、API/chat 进程读不到 → 主部署下
#: 门控无操作），故 Redis 锚点为主、此处降级为"Redis 不可用时的本进程兜底"。
_COVERED_KB_VERSION: str | None = None

#: covered 版本的 Redis 锚点 key（架构三期 2）。无 TTL——版本指纹单调递进
#: （就绪文档数:最新文档 created_at，见 chat._kb_version_str），每次覆盖检查通过即覆盖写，
#: 旧值天然被替代，无需过期淘汰。
_REDIS_COVERED_KEY = "quick:covered_kb_version"

#: Redis 不可用只警告一次（沿用 _WARNED_STALE_VERSION 的一次性去重模式，不随请求刷屏）；
#: 任一操作成功即重置——恢复后的再次故障会重新警告，避免长期静默。竞态最坏后果是
#: 多一条/少一条 warning，无正确性影响。
_REDIS_WARNED = False


#: 已对哪个漂移版本警告过（chat 禁用路径日志一次性：同版本重复请求不刷屏）。
_WARNED_STALE_VERSION: str | None = None


def _redis_set_covered(kb_version: str) -> None:
    """covered 版本写 Redis（跨进程锚点，Celery worker 写、API/chat 进程读）。

    fail-open（沿用 answer_cache 精确层先例）：失败仅警告一次，模块级状态已兜底，
    不阻塞导入。
    """
    global _REDIS_WARNED
    try:
        get_redis().set(_REDIS_COVERED_KEY, kb_version)
        _REDIS_WARNED = False
    except Exception:  # noqa: BLE001 - fail-open
        if not _REDIS_WARNED:
            _REDIS_WARNED = True
            logger.warning("covered 版本写 Redis 失败（跨进程门控暂不可用，退回模块级状态）", exc_info=True)


def _covered_version() -> str | None:
    """最近通过覆盖检查的 kb_version：Redis 优先（跨进程），缺失/不可用回退模块级。"""
    global _REDIS_WARNED
    try:
        value = get_redis().get(_REDIS_COVERED_KEY)
    except Exception:  # noqa: BLE001 - fail-open
        if not _REDIS_WARNED:
            _REDIS_WARNED = True
            logger.warning("covered 版本读 Redis 失败（回退模块级状态，仅本进程门控生效）", exc_info=True)
        return _COVERED_KB_VERSION
    _REDIS_WARNED = False  # 恢复可达：重置一次性警告
    if value:
        return value
    return _COVERED_KB_VERSION  # key 不存在（新实例/被清空）→ 模块级兜底


def check_kb_coverage(kb_text: str, kb_version: str | None = None) -> bool:
    """快捷话术 vs KB 覆盖检查（5-2 失效面门禁）：通过返回 True，否则 False。

    通过判据：有 KB 依据的话术占比 ≥ _COVERAGE_PASS_RATIO（即 uncovered_questions
    结果不过半）。通过且调用方
    补传 kb_version（knowledge_import_service 导入成功后按 chat._kb_version_str
    同式计算）时记录通过版本——双写：模块级（本进程兜底）+ Redis 锚点（跨进程生效，
    架构三期 2），作为 quick 短路的放行依据；未通过不记录 → 新版本在 chat 端被
    is_enabled_for 判为禁用。kb_version 为 None（无法锚定版本）时只返回判定结果、
    不记录状态。
    """
    uncovered = uncovered_questions(kb_text)
    passed = (len(_QA) - len(uncovered)) / len(_QA) >= _COVERAGE_PASS_RATIO
    if passed and kb_version is not None:
        global _COVERED_KB_VERSION
        _COVERED_KB_VERSION = kb_version  # 模块级始终记录（Redis 不可用时本进程仍生效）
        _redis_set_covered(kb_version)
    return passed


def is_enabled_for(kb_version: str | None) -> bool:
    """quick 预置话术对当前 kb_version 是否放行（chat.py 短路前校验）。

    通过版本的读序：Redis（跨进程，Celery worker 导入后写入）→ 模块级回退
    （Redis 不可用/key 缺失时的本进程兜底）→ 两者皆无 → 恒 True。

    - 从未通过覆盖检查 → True：现有环境无导入动作时行为与旧版完全一致（向后兼容）；
    - 当前 kb_version 为 None（无版本可比）→ True：无版本环境向后兼容，且不触碰
      Redis（chat 热路径零额外开销）；
    - 与最近通过版本一致 → True；KB 已变更而新版本未通过 → False（回落 RAG），
      并对该漂移版本 warning 一次（不随请求刷屏）。
    """
    if kb_version is None:
        return True
    covered = _covered_version()
    if covered is None or kb_version == covered:
        return True
    global _WARNED_STALE_VERSION
    if kb_version != _WARNED_STALE_VERSION:
        _WARNED_STALE_VERSION = kb_version
        logger.warning(
            "快捷话术对当前 KB 版本 %s 未通过覆盖检查（最近通过版本: %s），命中问题回落 RAG",
            kb_version,
            covered,
        )
    return False
