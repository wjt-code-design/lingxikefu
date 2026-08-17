"""QueryRewrite 规则层（T9-S2）：口语化查询 → 规范查询（仅服务检索，intent 恒用原文）。

设计（QueryRewrite设计-2026-08-16 §四）：
- 同义归一：口语/错别字 → 知识库用语（对齐评测集与 kb/ 文档用词）
- 方言词替换：仅安全映射（咋→怎么、啥→什么…），动作动词（整/弄/搞）语义不定，不映射（留 LLM 兜底）
- 语气词清洗：嗯/哈/呗/那个/就是说 等删除（保留 吗/吧/呢 疑问语气）
- 指代消解：会话级上轮实体（型号/商品词）替换 它/这个/那个/这
- 红线保护：数字/型号/否定/情绪词不参与改写（表内条目均为安全同义词；含数字上下文不指代消解）
- 顺序契约：intent 判定恒用原文（T1 分流已落实）；改写输出仅检索/缓存 key 使用
"""
from __future__ import annotations

import re

#: 同义归一：口语/错别字/缩略 → 知识库用语（逐条人工审定，防误伤）
SYNONYM_MAP: dict[str, str] = {
    # 保修（Q025-Q028/Q065-Q068）
    "质保": "保修",
    "维保": "保修",
    "联保": "保修",
    "三包": "保修",
    # 碎屏险（Q091/Q093/Q097）
    "碎屏显": "碎屏险",
    "碎屏修": "碎屏险",
    # 价保（Q076）
    "保价": "价保",
    # 退换货
    "退换": "退货",
    "退掉": "退货",
    # 发票缩略展开（Q018/Q059 用"增值税专用发票"）
    "专票": "增值税专用发票",
    "普票": "普通发票",
    # 维修
    "返修": "维修",
    # 配送
    "包邮": "免运费",
}

#: 方言词 → 普通话（安全映射；动作动词"整/弄/搞"语义不定不映射）
DIALECT_MAP: dict[str, str] = {
    "咋回事": "怎么回事",
    "咋弄": "怎么弄",
    "咋办": "怎么办",
    "咋": "怎么",
    "啥": "什么",
    "甭": "不用",
    "瞅": "看",
    "咋样": "怎么样",
}

#: 语气词/填充（删除；保留 吗/吧/呢 疑问语气）
FILLER_PATTERN = re.compile(r"(嗯|哈|呗|那个|就是说|的话|来着)")
#: 保留的疑问语气词（不可删）
_KEEP_TONE = ("吗", "吧", "呢")

#: 指代词（会话级消解）
_REFERENCE_RE = re.compile(r"(这个|那个|它|这)")
#: 商品词表（3C/家电，用于上轮实体提取）
_PRODUCT_TERMS = ("手机", "冰箱", "空调", "电视", "洗衣机", "电脑", "平板", "耳机", "充电器", "显示器", "笔记本")
#: 型号正则（字母+数字组合，支持多段如 Z9 Pro / X100 Ultra）
_MODEL_RE = re.compile(r"\b[A-Za-z]+\s?\d+[A-Za-z0-9]*(?:\s+[A-Za-z0-9]+)*\b")

#: 数字/型号保护（指代消解禁入上下文）
_HAS_NUMERIC = re.compile(r"\d")


def _extract_entities(query: str) -> list[str]:
    """从问句中提取候选实体：型号 + 商品词（供指代消解）。"""
    entities: list[str] = []
    for m in _MODEL_RE.finditer(query):
        entities.append(m.group(0).strip())
    for term in _PRODUCT_TERMS:
        if term in query:
            entities.append(term)
    return entities


def rewrite(query: str, history: list[dict] | None = None) -> tuple[str, dict]:
    """规则层改写：返回 (rewritten, meta)。meta 含 adopted/改动项，供审计与校验。

    - 无任何改动 → rewritten 原样、meta.adopted=False
    - 数字/型号/否定/情绪上下文不参与改写（表内条目审定安全；数字 query 不指代消解）
    """
    original = query
    changed: list[str] = []

    # 1) 同义归一（长词优先，防"保修"被"质保"误配后的级联）
    text = query
    for k in sorted(SYNONYM_MAP, key=len, reverse=True):
        if k in text:
            text = text.replace(k, SYNONYM_MAP[k])
            changed.append(f"syn:{k}→{SYNONYM_MAP[k]}")

    # 2) 方言替换
    for k in sorted(DIALECT_MAP, key=len, reverse=True):
        if k in text:
            text = text.replace(k, DIALECT_MAP[k])
            changed.append(f"dia:{k}→{DIALECT_MAP[k]}")

    # 3) 语气词清洗（保留 吗/吧/呢）
    if FILLER_PATTERN.search(text):
        text = FILLER_PATTERN.sub("", text)
        changed.append("fill")

    # 4) 指代消解（仅当无数字上下文 + 有上轮实体）
    if not _HAS_NUMERIC.search(query) and history:
        prev = next(
            (m["content"] for m in reversed(history) if m.get("role") == "user"),
            None,
        )
        if prev and _REFERENCE_RE.search(text):
            entities = _extract_entities(prev)
            if entities:
                for ref in ("这个", "那个", "它", "这"):
                    text = text.replace(ref, entities[0])
                changed.append(f"ref→{entities[0]}")

    rewritten = text.strip()
    adopted = rewritten != original or bool(changed)
    return rewritten, {"original": original, "rewritten": rewritten, "adopted": adopted, "changes": changed}
