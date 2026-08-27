"""Agent 层（v1.1 多 Agent 协作方案书 §2.3，对抗审查 2026-08-27 收敛）：

- base：Agent 抽象基类（统一 async run(ctx) 契约）
- router：Router——前置意图分类（单一真源）+ 规则分发 + 执行计划；
  计划只列真实编排成员（image / ticket），不再含永不执行的假成员
- ticket_agent：Ticket Agent——薄包装 services.ticket_service.ensure_active_ticket
  （幂等 + fail-open 已具备；async 契约 + 内部 run_in_threadpool）
- image_agent：Image Agent——图片理解（async run，火山视觉模型）

QA 问答路径不是 Agent 成员：chat 层直接调用 ``stream_answer``（流式生成与
断连/配额/落库耦合在 chat 层为 v1.1 方案书 §2.2 硬约束）。旧版 qa_agent
包装类无生产调用方（仅测试引用），已删除，避免"执行计划列了却永不执行"的假契约。

纪律：分类逻辑单一真源（rag_service.classify_intent），Router 只调用不复制；
Agent 不互相调用，仅经 SharedContext 交换数据。
"""
