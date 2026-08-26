"""会话状态机（批次B）：阶段跟踪 + 槽位填充。纯函数式，零 IO。

状态结构（sessions.conv_state JSON 列）：
    {"stage": str, "topic": str, "slots": {str: str}, "clarify_count": int}

设计要点：
- 主题词表复用 session_context._extract_topic_names（单一真源，不复制关键词）；
- 订单号提取复用 query_rewrite._ORDER_RE（既有正则，支持 SO2026080118 / XOZ-12345）；
- 槽位只增不删：用户中途补充的实体保留（客服交接溯源有用）；
- 主题可切换：最新消息命中新主题则切换，未命中保留（闲聊不清空）；
- update 返回新 dict，禁止原地修改（调用方可能持有旧引用）。
批次 C（Clarify）依赖 clarify_count/STAGE_CLARIFYING；批次 D（订单工具）依赖 slots.order_no。
"""
from __future__ import annotations

from app.services.query_rewrite import _ORDER_RE
from app.services.session_context import _extract_topic_names

#: 阶段常量（stage 取值域）
STAGE_GREETING = "greeting"          # 无明确主题
STAGE_COLLECTING = "info_collecting"  # 有主题，缺必需槽位
STAGE_RESOLVING = "resolving"        # 槽位齐或无需槽位
STAGE_CLARIFYING = "clarifying"      # 已发澄清问句，等用户回复（批次C 使用，本批次只定义）

#: 槽位名常量（大扫查修复：消除 "order_no" 魔法串散布 3 模块）
SLOT_ORDER_NO = "order_no"

#: 主题 → 必需槽位（首期仅 order_no 一个槽位，YAGNI；主题名与 session_context.FLOW_TOPICS 一致）
REQUIRED_SLOTS: dict[str, list[str]] = {
    "退款": [SLOT_ORDER_NO],
    "退换货": [SLOT_ORDER_NO],
    "保修/维修": [SLOT_ORDER_NO],
    "配送/物流": [SLOT_ORDER_NO],
    "价保": [SLOT_ORDER_NO],
    "发票": [],
}

#: 每会话澄清追问上限（批次C 读取；此处定义避免跨模块常量漂移）
MAX_CLARIFY = 2


def new_state() -> dict:
    """初始状态。"""
    return {"stage": STAGE_GREETING, "topic": "", "slots": {}, "clarify_count": 0}


def update(state: dict | None, message: str) -> dict:
    """推进状态：主题判定（最新消息优先）→ 槽位提取（只增不删）→ 阶段推进。

    - state=None 视为初始状态（旧会话 conv_state 为 NULL 的场景）；
    - 返回新 dict，不修改入参；
    - 主题未命中新词 → 保留旧主题（闲聊/追问回复不清空主题）；
    - 阶段：无主题 greeting；有主题缺槽位 collecting；齐了 resolving。
      clarifying 是瞬态（等用户回复），收到新消息即按主题/槽位重新判定。
    """
    s = dict(state or new_state())
    slots = dict(s.get("slots") or {})

    # 1) 主题：最新消息命中则切换（多个命中取词表优先级第一个，与 extract_topic 行为一致）
    topics = _extract_topic_names([{"role": "user", "content": message}])
    if topics:
        s["topic"] = topics[0]

    # 2) 槽位：订单号——新替旧（P3-⑬）：识别到新订单号时替换当前槽位（保留最近一个、
    #    正在处理的那单），旧号移入 past_entities 供指代回溯/交接（多单咨询逐单处理）。
    for m in _ORDER_RE.finditer(message):
        order_no = m.group(0).strip()
        if order_no:
            prev = slots.get(SLOT_ORDER_NO)
            slots[SLOT_ORDER_NO] = order_no
            if prev and prev != order_no:
                past = list(s.get("past_entities") or [])
                if prev not in past:
                    past.append(prev)
                s["past_entities"] = past
            break  # 每轮只采纳最新订单号
    s["slots"] = slots

    # 3) 阶段推进
    if not s.get("topic"):
        s["stage"] = STAGE_GREETING
    elif missing_slots(s):
        s["stage"] = STAGE_COLLECTING
    else:
        s["stage"] = STAGE_RESOLVING
    return s


def missing_slots(state: dict) -> list[str]:
    """当前主题缺失的必需槽位名；无主题/主题无要求返回 []。"""
    required = REQUIRED_SLOTS.get(state.get("topic", ""), [])
    slots = state.get("slots") or {}
    return [name for name in required if name not in slots]


def to_prompt_hint(state: dict | None) -> str | None:
    """注入 RAG prompt 的状态文本（经 build_qa_messages 的 context_hint 通道，M10 分隔块内声明为数据）。

    返回如「会话主题：退款；已提供订单号：SO2026080118」或（未提供时）「会话主题：退款；订单号：未提供」。
    state 为 None / 无主题返回 None（不注入，输出与旧版一致）。
    """
    if not state or not state.get("topic"):
        return None
    parts = [f"会话主题：{state['topic']}"]
    if SLOT_ORDER_NO in (state.get("slots") or {}):
        parts.append(f"已提供订单号：{state['slots'][SLOT_ORDER_NO]}")
    elif SLOT_ORDER_NO in REQUIRED_SLOTS.get(state["topic"], []):
        parts.append("订单号：未提供")
    return "；".join(parts)


def mark_clarifying(state: dict | None) -> dict:
    """澄清轮状态转移（大扫查修复：状态变更逻辑收回单一真源，chat 层不再内联改写）。

    stage=clarifying + clarify_count+1；返回新 dict，不修改入参。
    """
    s = dict(state or new_state())
    s["stage"] = STAGE_CLARIFYING
    s["clarify_count"] = int(s.get("clarify_count", 0)) + 1
    return s
