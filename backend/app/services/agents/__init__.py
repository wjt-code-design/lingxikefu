"""Agent 层（v1.1 多 Agent 协作方案书 §2.3）：

- base：Agent 抽象基类（统一 run(ctx) 契约）
- qa_agent：QA Agent——封装现有 RAG 非流式管线（run_pipeline），intent 已由 Router 前置
- ticket_agent：Ticket Agent——薄包装 ensure_active_ticket（幂等 + fail-open 已具备）
- image_agent：Image Agent——图片理解占位（当前无图片通道，恒降级留痕）
- router：Router——前置意图分类（单一真源）+ 规则分发 + 事件编排策略

纪律：分类逻辑单一真源（rag_service.classify_intent），Router 只调用不复制；
Agent 不互相调用，仅经 SharedContext 交换数据。
"""
