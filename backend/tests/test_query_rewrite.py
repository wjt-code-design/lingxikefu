"""QueryRewrite 规则层测试（T9-S2）：归一 / 方言 / 语气词 / 指代 / 红线 / 意图保持。"""
from __future__ import annotations

from app.services.query_rewrite import _ORDER_RE, rewrite
from app.services.rag_service import classify_intent


def test_synonym_normalization():
    """同义归一：错别字/口语 → 知识库用语。"""
    r, meta = rewrite("碎屏显怎么换")
    assert "碎屏险" in r and meta["adopted"]
    r2, _ = rewrite("商品维保多久")
    assert "保修" in r2 and "维保" not in r2
    r3, _ = rewrite("能开专票吗")
    assert "增值税专用发票" in r3


def test_dialect_replacement():
    """方言 → 普通话（安全映射）。"""
    r, _ = rewrite("咋退货")
    assert "怎么退货" == r
    r2, _ = rewrite("手机啥处理器")
    assert "什么处理器" in r2


def test_filler_removal_keeps_question_tone():
    """语气词删除，但保留疑问语气（吗）。"""
    r, _ = rewrite("嗯 那个 保修多久呗")
    assert "保修多久" in r and "嗯" not in r and "那个" not in r
    r2, _ = rewrite("退款能到账吗")
    assert "吗" in r2  # 疑问语气保留


def test_no_change_returns_original():
    """无改动 → adopted=False，原样返回。"""
    r, meta = rewrite("退款多久能到账")
    assert r == "退款多久能到账" and not meta["adopted"]


def test_redline_numbers_and_models_untouched():
    """红线：数字/型号不参与改写。"""
    r, _ = rewrite("保修 12 个月还是 24 个月")
    assert "12" in r and "24" in r
    r2, _ = rewrite("星河 Z9 Pro 保修多久")
    assert "Z9 Pro" in r2


def test_coreference_resolution():
    """指代消解：上轮实体替换 它/这个（无数字上下文）。"""
    history = [{"role": "user", "content": "星河 Z9 Pro 手机续航怎么样"}]
    r, meta = rewrite("它保修多久", history=history)
    assert "Z9 Pro" in r or "手机" in r
    assert meta["adopted"]


def test_coreference_resolution_business_order():
    """2026-08-21 业务实体：上轮订单号，本轮"这个订单"消解为订单号（最具体优先）。"""
    history = [{"role": "user", "content": "我的订单 XOZ-12345 到哪了"}]
    r, meta = rewrite("这个订单怎么退款", history=history)
    assert "XOZ-12345" in r, r
    assert meta["adopted"]

    history2 = [{"role": "user", "content": "订单 SO2026080118 显示派送中"}]
    r2, _ = rewrite("它什么时候到", history=history2)
    assert "SO2026080118" in r2, r2


def test_order_extraction_cjk_adjacency():
    """H1 修复：_ORDER_RE 的 \\b 在 Unicode 模式下 CJK 属 \\w → 汉字紧贴订单号时边界不成立，漏提取。

    期望来源=手工复现（修前 3 条 miss）；对照锚（带空格）修前即命中，修后必须保持。
    负例：长 ASCII 标识符中段不提取（AXOZ1234567 整串本就是订单形、修前即整串命中，
    lookaround 只放行汉字邻接，故断言=恰好整串命中、不拆出中段）。
    """
    assert _ORDER_RE.findall("订单SO2026080118到哪了") == ["SO2026080118"]
    assert _ORDER_RE.findall("我的订单SO2026080118退款") == ["SO2026080118"]
    assert _ORDER_RE.findall("XOZ-12345退款了吗") == ["XOZ-12345"]
    assert _ORDER_RE.findall("订单 XOZ-12345 退款") == ["XOZ-12345"]  # 对照锚
    assert _ORDER_RE.findall("ABCXOZ-12345DEF") == []  # ASCII 中段不提
    assert _ORDER_RE.findall("AXOZ1234567") == ["AXOZ1234567"]  # 整串命中，无中段误拆


def test_coreference_cjk_adjacent_order():
    """H1 行为受益：上轮贴汉字订单号（修前漏提取）→ 本轮指代消解可用。"""
    history = [{"role": "user", "content": "订单SO2026080118到哪了"}]
    r, meta = rewrite("它什么时候到", history=history)
    assert "SO2026080118" in r, r
    assert meta["adopted"]


def test_intent_preserved_after_rewrite():
    """意图保持：改写后 classify_intent 与原句一致（口语集抽样）。"""
    samples = [
        ("我要投诉找经理", "我要投诉找经理"),  # handoff 不变
        ("碎屏显咋换", "碎屏险怎么换"),  # qa 意图稳定
        ("你们服务太差要退钱", "你们服务太差要退钱"),  # 情绪 handoff 不参与改写
    ]
    for orig, rew in samples:
        assert classify_intent(orig) == classify_intent(rew), (orig, rew)
