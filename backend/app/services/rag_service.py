"""RAG 管线（BU-03/BU-05 核心）：intent → 检索 → prompt → 流式生成。

设计（vet-plan 裁定）：
- 纯函数式 4 步，不做 LangGraph / query rewrite / sparse / rerank（MVP 关闭，
  接口预留开关，recall 基线不达标再开）。
- intent 用规则式（轻量、省 LLM 调用）：闲聊/转人工关键词命中即短路，
  不再调 LLM 做意图分类（单租户客服场景关键词足够）。
- 诚实性：检索 top-1 分数低于阈值 → 拒答提示转人工，绝不编造（fail-closed）。
- P2-1：检索不可用/管线超时（RetrievalError/PipelineTimeoutError）→ fail-open 降级为
  诚实拒答（retrieve_degraded=True），不再抛 RagError 转 error 事件——
  用户得到可操作引导（转人工），而非"服务暂不可用"；RagError 仅留作防御性兜底。
  架构一期 5（降级话术阶梯）：两类降级拆分异常路径（degraded_kind=retrieval/timeout），
  话术分档——检索故障=系统坏了该转人工，管线超时=容量延迟该稍后再试；均保评测锚点。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

from fastapi.concurrency import run_in_threadpool

from app.core.config import settings
from app.llm_clients.chat import get_chat_client
from app.orchestrator import PipelineTimeoutError
from app.prompts.qa_prompt import build_qa_messages
from app.services.clarify import ClarifyError, generate_clarify
from app.services.pipeline import Pipeline
from app.services.retrieval_service import RetrievalError, RetrievedChunk
from app.services.session_context import extract_topic
from app.utils.text_splitter import clean_snippet

logger = logging.getLogger(__name__)


class RagError(Exception):
    """RAG 管线失败（Chat 层应转 SSE error，不静默）。"""


#: 转人工关键词（规则式 intent，命中即短路，不检索不调 LLM）。
#: 仅保留明确短语，避免单字/通用词误命中（见 M6）。
HANDOFF_KEYWORDS = (
    "转人工", "人工客服", "人工服务", "找人工", "投诉", "经理", "真人客服",
)
#: 情绪/强烈不满词（T1 分流升级）：命中即 handoff（高优建单）。
#: 注意：`退款` 是 qa 高频词（Q005/Q024 退款到账为问答题），**不得**入此表；
#: `投诉` 已在 HANDOFF，不重复。情绪词与 HANDOFF 分离，保持语义单一。
EMOTIONAL_KEYWORDS = (
    "退钱", "赔偿", "太慢", "差评", "气死", "骗子", "欺诈",
    "立刻解决", "马上解决", "马上处理", "服务太差", "受不了", "垃圾", "投诉无门",
    # 2026-08-21 情绪词扩充：显式情绪词命中 → handoff（高优转人工，先安抚再转）
    "生气", "愤怒", "气人", "恼火", "火大", "发火", "很烦", "烦死",
    "很不爽", "太气人", "气坏了", "气炸",
    # 2026-08-21 口语化/网络化情绪表达扩充（避开单字防误伤正常问答）
    "烦死了", "太烦了", "无语", "好气", "崩溃", "气死我了",
    # 2026-08-21 责骂/质问式情绪表达扩充（抱怨态度差 → handoff 高优）
    "干什么吃的", "怎么搞的", "搞什么名堂",
)
#: S2 窄语境排除（外部审查 S2）：商品/故障语境短语不判情绪转人工。
#: 实现=残句复扫：剔除语境短语后对剩余文本重查情绪词表——只放行语境本身，
#: 同句其他情绪信号（"气死了""骗子""赔偿""退钱""差评"）仍命中转人工；
#: HANDOFF_KEYWORDS（投诉/转人工等）判定在此之前的独立分支，不受本排除影响。
_EMOTION_EXCLUDE = re.compile(
    r"垃圾(袋|桶|处理器|分类)"               # 商品名词：垃圾袋/垃圾桶/垃圾处理器/垃圾分类
    r"|(系统|软件|应用|程序|手机).{0,4}崩溃"  # 故障语境的"崩溃"
    r"|(运行|速度|响应|加载).{0,4}太慢"       # 性能语境的"太慢"
)
CHITCHAT_KEYWORDS = (
    "你好", "在吗", "谢谢", "再见", "你是谁", "你是机器人",
    "天气", "笑话", "几点下班", "下班", "心情",
)
#: D3（2026-09-03 审计）：chitchat 判定收边界——裸子串匹配会把「退款多久到账，谢谢」
#: 这类「业务问句 + 礼貌词」误判成 chitchat（不检索不调 LLM，业务被静默吞掉）。
#: 落法=残句复扫（同 _EMOTION_EXCLUDE 思路）：剔除 chitchat 词 + 封闭助词表后，
#: 剩余实义文本为空才判 chitchat；非空 → 含真实业务内容 → 走 qa。
#: 「天气怎么样」「讲个笑话」等纯闲聊经助词表归空仍 chitchat（行为保持）。
_CHITCHAT_FILLER = (
    "你们", "怎么样", "怎样", "怎么", "讲个", "说个", "聊聊", "说说",
    "来一个", "一句", "一下", "一个", "吗", "呢", "吧", "啊", "哦", "呀", "哈", "嘛",
)
#: 长→短排序防 alternation 前缀吞尾（如「几点下班」须先于「下班」整体剔除）
_CHITCHAT_STRIP = re.compile("|".join(sorted([*CHITCHAT_KEYWORDS, *_CHITCHAT_FILLER], key=len, reverse=True)))
#: 剩余文本清零判定：纯标点/空白 = 无实义内容
_PUNCT_WS = re.compile(r"[\s，。！？；：、,.!?;:'\"（）()【】\[\]~～…\u3000]+")


def _is_chitchat(query: str) -> bool:
    """chitchat 边界判定：剔除词表词 + 助词后无实义剩余 → 纯寒暄。"""
    residual = _CHITCHAT_STRIP.sub("", query)
    return not _PUNCT_WS.sub("", residual)

#: M6：裸「人工」词边界匹配，但排除「人工智能」（避免误判 handoff）。
_RE_ARTIFICIAL = re.compile(r"人工(?!智能)")

#: 检索分数阈值在 app.core.config.settings.MIN_SCORE（单一真源，run_pipeline 直接读）


@dataclass
class RagResult:
    """管线产物：Chat 层据此发 SSE 事件。"""

    intent: str  # qa | handoff | chitchat
    chunks: list[RetrievedChunk] = field(default_factory=list)
    refuse: bool = False  # 无依据拒答（诚实性）
    refuse_reason: str = ""
    from_cache: bool = False  # T10：答案缓存命中（不走 LLM）
    cached_answer: str = ""  # T10：缓存答案全文
    cached_sources: list[dict] = field(default_factory=list)  # T10：缓存 sources（含 doc_title）
    rewritten_query: str = ""  # T9：检索/缓存 key 用的改写后文本
    retrieve_degraded: bool = False  # P2-1：检索不可用/管线超时被降级（fail-open 拒答路径）
    # 降级阶梯（架构一期 5）：""=非降级 | "retrieval"=检索故障（服务坏了）| "timeout"=管线超时（容量延迟）。
    # 默认 ""：retrieve_degraded=True 而未标 kind 的旧构造方沿用检索故障话术（向后兼容）。
    degraded_kind: str = ""


def classify_intent(query: str) -> str:
    """规则式意图分类：handoff(人工+情绪) > chitchat > qa（T1 分流升级：情绪词并入 handoff）。

    S2 窄语境排除：商品/故障语境（垃圾袋/系统崩溃/运行太慢等）先剔除再复扫情绪词表
    （残句复扫，只放行语境本身，同句情绪信号词仍转人工）；HANDOFF_KEYWORDS/
    _RE_ARTIFICIAL 判定在最前，投诉类词不受影响。
    """
    if _RE_ARTIFICIAL.search(query) or any(k in query for k in HANDOFF_KEYWORDS):
        return "handoff"
    if any(k in _EMOTION_EXCLUDE.sub("", query) for k in EMOTIONAL_KEYWORDS):
        return "handoff"
    # D3：残句复扫边界（见 _is_chitchat）——裸子串改边界判定，防业务问句误吞
    if _is_chitchat(query):
        return "chitchat"
    return "qa"


def _build_pipeline(pipeline: Pipeline) -> Pipeline:
    """内部：用 PipelineRunner 编排可组合节点（条件短路 + 节点重试）"""
    from app.orchestrator import PipelineRunner
    from app.services.steps.cache_check import check_cache
    from app.services.steps.intent import classify_intent as _classify_intent
    from app.services.steps.refuse import check_refuse
    from app.services.steps.retrieve import retrieve_chunks
    from app.services.steps.rewrite import rewrite_query

    runner = PipelineRunner(retry=1)
    return runner.run(
        pipeline,
        nodes=[
            _classify_intent,
            rewrite_query,
            check_cache,
            retrieve_chunks,
            check_refuse,
        ],
    )


def run_pipeline(query: str, kb_id: UUID, top_k: int | None = None, history: list[dict] | None = None, kb_version: str | None = None) -> RagResult:
    """RAG 管线入口（非流式部分）：intent(原文) → 缓存/检索(改写后) → 拒答判定。

    顺序契约（T9-S3）：intent 恒用原文判定；改写只服务检索与缓存 key（query_rewrite）。
    T10：kb_version 由调用方（chat 层查 KB.updated_at）传入，缓存命中即返回（不走 LLM）。
    自 2026-08-21：top_k 默认跟随 settings.RETRIEVAL_TOP_K（单一真源；外部显式传参可覆盖）。
    返回 RagResult，生成阶段由 Chat 层用 build_qa_messages 组装后流式调用。
    """
    try:
        pipeline = Pipeline(query=query, kb_id=kb_id, history=history or [], kb_version=kb_version)
        pipeline = _build_pipeline(pipeline)
    except RetrievalError as e:
        # P2-1：检索不可用 → 降级为诚实拒答（fail-open），不把整条问答链打成 error 事件。
        # 内部重试已在 Runner 内完成（retry=1），走到这里即重试后仍失败。
        logger.warning("检索服务不可用，降级拒答引导转人工: %s", e)
        return RagResult(
            intent="qa",
            refuse=True,
            refuse_reason="检索服务暂不可用",
            retrieve_degraded=True,
            degraded_kind="retrieval",
        )
    except PipelineTimeoutError as e:
        # 降级阶梯（架构一期 5）：时间预算用尽 ≠ 检索故障——超时多为容量/延迟问题，
        # 用户该"稍后再试"；检索故障才是"系统坏了该转人工"。拆分异常路径后 _no_llm_reply 分档出话术。
        logger.warning("管线时间预算用尽，降级拒答（容量话术）: %s", e)
        return RagResult(
            intent="qa",
            refuse=True,
            refuse_reason="管线响应超时",
            retrieve_degraded=True,
            degraded_kind="timeout",
        )

    # 映射回 RagResult（chat.py 现有调用不变）
    return RagResult(
        intent=pipeline.intent,
        chunks=pipeline.chunks,
        refuse=pipeline.refuse,
        refuse_reason=pipeline.refuse_reason,
        from_cache=pipeline.from_cache,
        cached_answer=pipeline.cached_answer,
        cached_sources=pipeline.cached_sources,
        rewritten_query=pipeline.rewritten_query,
    )


async def stream_answer(
    query: str,
    kb_id: UUID,
    history: list[dict] | None = None,
    top_k: int | None = None,
    kb_version: str | None = None,
    user_profile: str | None = None,
    state_hint: str | None = None,
    clarify_left: int | None = None,
):
    """流式回答：yield (event_type, data)。

    event_type: intent | stage | token | sources | done | error
    - intent{intent,refuse} → stage/retrieving → stage/generating → sources → token* → done
    - 拒答/闲聊/转人工 不发 token，直接 sources([])+done
    - T10 缓存命中：intent → stage* → token*(缓存答案分片) → sources(缓存) → done(cache_hit=true)
    - 任一异常 → error（fail-closed，不静默）
    - user_profile（可选，2026-08-22 Phase C）：画像文本，透传 build_qa_messages 注入
      <<用户画像>> 块；None 不注入（输出与旧版一致）。仅影响 prompt，不影响缓存 key。
    - state_hint（可选，批次B）：会话状态机提示（主题+槽位），优先级高于 extract_topic
      兜底；None 时回退 extract_topic（旧行为），且不进缓存 key（仅影响 prompt）。
    - clarify_left（可选，批次C）：剩余澄清次数（None=调用方不允许澄清，行为同旧版）。
      拒答且 clarify_left>0 时先尝试生成澄清问句（done 带 clarify=True）；
      生成失败落回原拒答（fail-open）。
    """
    result = RagResult(intent="qa")
    top_k = settings.RETRIEVAL_TOP_K if top_k is None else top_k
    try:
        # H2 修复：run_pipeline 内含阻塞式 embedding（model.encode），搬出事件循环
        # T9-S3：history 传入供指代消解（检索用改写，intent 用原文）；kb_version 供 T10 缓存校验
        result = await run_in_threadpool(
            run_pipeline, query, kb_id, top_k=top_k, history=history, kb_version=kb_version
        )
    except RagError:
        # 防御性兜底（P2-1 后 run_pipeline 已 fail-open 降级，正常不再触发）：
        # 一旦未来回归抛 RagError，对外 SSE 仍只给通用文案，不转发原始异常
        # （内网地址/细节可能被注入）。
        logger.exception("RAG 检索/管线失败")
        yield ("error", {"code": "RAG_RETRIEVAL", "message": "知识库检索暂不可用，请稍后重试"})
        return

    # 批次C：拒答前先澄清（clarify_left>0 时）——fail-open，失败落回原拒答。
    # 澄清分支自带完整事件序列（intent 不标拒答），故须在统一 intent 事件之前，
    # 且所有 yield 均在 generate_clarify 成功之后——异常时无半截流。
    if result.refuse and result.intent == "qa" and (clarify_left or 0) > 0:
        # P2-1：检索降级时跳过澄清——澄清是"材料足够但要细节"的对话，检索不可用
        # 时应收敛到可操作的降级话术（引导转人工），而不是假装正常地追问细节。
        if not result.retrieve_degraded:
            try:
                question = await generate_clarify(query, result.chunks)
                yield ("intent", {"intent": "qa", "refuse": False})
                yield ("stage", {"stage": "retrieving", "msg": "已检索知识库"})
                yield ("stage", {"stage": "generating", "msg": "正在生成回答"})
                # 大扫查O3：整段单 token 下发（与订单工具分支同构）——8 字分片会把
                # 「订单号」等实体词拆散到相邻 SSE 事件，前端逐帧渲染出现断裂观感。
                yield ("token", {"delta": question})
                yield ("sources", {"sources": []})
                yield ("done", {"message_id": "", "clarify": True})
                return
            except ClarifyError as e:
                logger.warning("澄清问句生成失败（%s），落回原拒答路径", e)
            except Exception:  # noqa: BLE001 - 意外异常同样 fail-open 回退，不产生半截流
                logger.exception("澄清问句生成意外异常，落回原拒答路径")

    # R-2：真实意图事件（chat 层据此落库 message.intent，不再写死 qa）
    yield ("intent", {"intent": result.intent, "refuse": result.refuse})
    yield ("stage", {"stage": "retrieving", "msg": "已检索知识库"})
    yield ("stage", {"stage": "generating", "msg": "正在生成回答"})

    # T10：缓存命中 → 直接分片输出缓存答案（省 LLM + 提速），sources 用缓存
    if result.from_cache:
        answer = result.cached_answer or ""
        for delta in _split_tokens(answer):
            yield ("token", {"delta": delta})
        yield ("sources", {"sources": result.cached_sources})
        yield ("done", {"message_id": "", "cache_hit": True})
        return

    if result.intent != "qa" or result.refuse:
        msg = _no_llm_reply(result)
        yield ("sources", {"sources": []})
        for delta in _split_tokens(msg):
            yield ("token", {"delta": delta})
        # Chat 层回填答案缓存时复用该 key；避免在 done 分支再次执行 rewrite。
        # 这是内部流事件字段，Chat 只向前端转发自己的 done 数据，因而不扩展 SSE 契约。
        yield ("done", {"message_id": "", "rewritten_query": result.rewritten_query})
        return

    try:
        topic = extract_topic(history)  # 兜底：无状态提示时维持旧行为
        messages = build_qa_messages(
            query=query,
            chunks=result.chunks,
            history=history or [],
            context_hint=state_hint or topic,  # 批次B：状态机提示优先
            profile=user_profile,
        )
        client = get_chat_client()
        # 不传 model：让 OpenAILikeChatClient 用自己的 _default_model()（唯一 provider longcat，模型名单一真源）
        # 思维链透传（感知 TTFT）：reasoning 先于 content 到达（~2s），Chat 层转发前端展示
        # "思考中"；reasoning 不进缓存/不落库，仅作为流式过程反馈。
        parts: list[str] = []
        async for kind, delta in client.stream_events(messages):
            if kind == "reasoning":
                yield ("reasoning", {"delta": delta})
            else:
                parts.append(delta)
                yield ("token", {"delta": delta})
        yield ("sources", {"sources": _to_sources(result.chunks)})
        # 引用编号修复（确定性）：流式已发原始 [来源N]，落库/缓存用校正后全文（fixed_content）。
        # Chat 层回填答案缓存时复用该 key；避免在 done 分支再次执行 rewrite。
        # 这是内部流事件字段，Chat 只向前端转发自己的 done 数据，因而不扩展 SSE 契约。
        fixed_content = fix_citations("".join(parts), result.chunks) if parts else ""
        yield ("done", {"message_id": "", "rewritten_query": result.rewritten_query, "fixed_content": fixed_content})
    except Exception:  # noqa: BLE001
        logger.exception("RAG 生成失败")
        yield ("error", {"code": "RAG_GENERATE", "message": "回答生成失败，请稍后重试"})


def _no_llm_reply(result: RagResult) -> str:
    if result.retrieve_degraded:
        # 降级话术阶梯（架构一期 5）：先按 degraded_kind 分档，再落原 handoff/chitchat/兜底分支。
        # - retrieval：检索服务坏了 → 故障话术；timeout：时间预算用尽（容量/延迟）→ 容量话术。
        # - degraded_kind 为空但 retrieve_degraded=True（旧构造方）沿用检索故障文案（向后兼容）。
        # - 两档均含「转人工」锚点（eval_faithfulness.REFUSE_MARKERS），缺失会致诚实性评测假阳性。
        if result.degraded_kind == "timeout":
            return "当前咨询量较大，回复出现延迟。您可以稍后再试，或转人工客服立即处理。"
        return "知识库检索服务暂时不可用，请稍后重试；如急需处理，可转人工客服帮您解决。"
    if result.intent == "handoff":
        return "很抱歉给您带来不好的体验。已为您转接人工客服，请稍候；您也可以描述具体问题，我会先尽力帮您解决。"
    if result.intent == "chitchat":
        return "我是星河智家智能客服，可以帮您解答退换货、保修、配送等问题。有什么可以帮您？"
    return "抱歉，我暂时没有找到关于这个问题的可靠信息，为避免误导您，建议转人工客服处理。"


def _to_sources(chunks: list[RetrievedChunk]) -> list[dict]:
    return [
        {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "score": round(c.score, 4),
            # 字段名对齐前端契约 MessageSource.snippet（SSE 契约 §1.4）；
            # UI 审查高2：清洗 markdown + 句界截断，避免来源面板渲染源码
            "snippet": clean_snippet(c.text),
        }
        for c in chunks
    ]


def _split_tokens(text: str, size: int = 8) -> list[str]:
    """非流式回复切成小片模拟流式（前端 SSE 展示流畅）。"""
    return [text[i : i + size] for i in range(0, len(text), size)]


#: 引用可溯源阈值（与 eval_faithfulness 判定同口径：2字窗口交集≥30%）
_CIT_OVERLAP_THRESHOLD = 0.30
#: 连续中文 ≥2 字（与 eval_faithfulness._bigrams 同口径：只切中文，数字/标点/换行不参与）
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def _sentence_overlap(sentence: str, chunk_text: str) -> float:
    """引用点句子与 chunk 的 2 字窗口交集比例（0-1）。

    与 eval_faithfulness 判定完全同口径（2026-09-02 修口径分歧）：仅对连续中文切 2 字
    窗口，忽略数字/标点/换行——否则 fix_citations 与 judge_citations 在阈值边界分歧
    （Q052 长句段落实测：全字符 0.543 vs 中文口径 0.27），导致保留 eval 判定无效的引用。
    """
    s_bg: set[str] = set()
    for w in _CJK_RE.findall(sentence):
        for i in range(len(w) - 1):
            s_bg.add(w[i : i + 2])
    c_bg: set[str] = set()
    for w in _CJK_RE.findall(chunk_text):
        for i in range(len(w) - 1):
            c_bg.add(w[i : i + 2])
    if not s_bg:
        return 0.0
    return len(s_bg & c_bg) / len(s_bg)


def fix_citations(answer: str, chunks: list[RetrievedChunk]) -> str:
    """引用编号修复（确定性后处理，非 LLM）：把 [来源N] 校正到实际支撑该句的 chunk。

    背景（2026-09-02 全量 eval citation 7 个失败点归因）：LongCat 两类错标——
    ①[来源1] 默认引用（内容在其他 chunk 仍标 [来源1]，prompt 规则已修）；②同文档续条
    锚定带标题首条（Q074 型，prompt 手段无效）。代码级兜底：逐点计算引用点句子与各
    chunk 的 2 字窗口交集，错标改到支撑最强的 chunk；无任何 chunk 支撑（编造引用）则
    摘除标记。只改编号/摘除，不动句子文本 → 不影响 answer 语义（faithfulness 判定）。
    连续引用 [来源N][来源M] 共享同一引用点句子，逐个校正（eval 判定对空句跳过不计）。
    """
    if not chunks or "[来源" not in answer:
        return answer
    parts = re.split(r"(\[来源\d+\])", answer)
    out: list[str] = []
    cur = ""
    sentence = ""  # 当前标记的引用点句子（跨连续标记共享）
    for part in parts:
        m = re.fullmatch(r"\[来源(\d+)\]", part)
        if m is None:
            cur += part
            segs = [s for s in re.split(r"(?<=[。！？；])", cur.strip()) if s.strip()]
            if segs:
                sentence = segs[-1]
            continue
        if cur:
            out.append(cur)
            cur = ""
        if sentence:
            best_i, best_ov = 0, 0.0
            for i, c in enumerate(chunks):
                ov = _sentence_overlap(sentence, c.text)
                if ov > best_ov:
                    best_i, best_ov = i, ov
            if best_ov >= _CIT_OVERLAP_THRESHOLD:
                out.append(f"[来源{best_i + 1}]")
            else:
                out.append("")  # 无任何 chunk 支撑 → 摘除（防编造引用残留）
        else:
            out.append(part)  # 引用点为空（句首标记）→ 原样保留（eval 判定不计）
    if cur:
        out.append(cur)
    return "".join(out)
