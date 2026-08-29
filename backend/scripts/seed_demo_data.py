"""灌入模拟真实场景的演示数据到知识库（物流/退款/送装/保修/配送）。

用法：python scripts/seed_demo_data.py [kb_id]
不传 kb_id 只选 name=='demo' 的库（空环境自动建 demo；只有业务库时拒绝，须显式指定 kb_id）。
幂等：sha256 去重，重复运行不会产生重复文档。
"""
import hashlib
import logging
import os
import sys
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# 允许从 scripts/ 子目录直接运行：把 backend 根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 使用 setdefault（而非强覆盖）：已注入的 hostname env（如容器内 postgres/redis/qdrant）优先，
# 否则回退本机 localhost。防脏 env 覆盖 .env 真实值；容器内也能一次跑通。
# 密钥不进代码：LONGCAT_API_KEY 由 .env / 环境提供，缺失时 llm_clients 报
# ModelNotConfiguredError（fail-loud，不静默用默认值跑错 Key）。
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

import sqlalchemy
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase
from app.repositories.document_repo import DocumentRepository, KnowledgeBaseRepository
from app.services.knowledge_import_service import ImportError_, import_document

DEMO_DIR = Path(__file__).parent / "demo_data"


def latest_kb(db) -> uuid.UUID:
    """无参 seed 的目标库选择（收权版，2026-08-28 污染事故防线）：

    1) 空环境 → 自动建 demo 库（CI unit tests 无库分支，保留）；
    2) 有 name=='demo' 库 → 选它（演示数据归位）；
    3) 只有业务库 → 拒绝并退出，要求显式 kb_id。
       事故复盘：旧逻辑取「租户最新库」，评测库恰为最新时 9 个演示文档混入
       「星河智家·官方政策库」，检索分布漂移致本地/CI 口径分裂（BASELINE §五）。

    无参选择仅在单租户（TENANT_DEFAULT）语境下安全；多租户化需显式 tenant 维度
    （终审建议 2026-08-28）。
    """
    kbs = db.scalars(
        sqlalchemy.select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
    ).all()
    if not kbs:
        demo = KnowledgeBase(
            name="demo",
            description="自动创建：承载演示订单数据，供订单检索集成回归（test_demo_orders.py）",
            tenant_id=settings.TENANT_DEFAULT,
        )
        db.add(demo)
        db.commit()
        db.refresh(demo)
        logger.info("自动创建 demo 知识库 %s", demo.id)
        return demo.id
    demo_kb = next((k for k in kbs if k.name == "demo"), None)
    if demo_kb is not None:
        return demo_kb.id
    listing = "\n".join(f"  {k.id}  {k.name}" for k in kbs)
    raise SystemExit(
        "[seed_demo_data] 拒绝自动选库：环境无 demo 库，现有库：\n"
        f"{listing}\n"
        "为防演示数据污染评测库（2026-08-28 事故），请显式指定目标："
        "python scripts/seed_demo_data.py <kb_id>（可先建独立 demo 库后再 seed）"
    )


def main() -> None:
    db = SessionLocal()
    try:
        kb_id = uuid.UUID(sys.argv[1]) if len(sys.argv) > 1 else latest_kb(db)
        print(f"目标 KB: {kb_id}")
        doc_repo = DocumentRepository(db)
        kb_repo = KnowledgeBaseRepository(db)
        if kb_repo.get(kb_id) is None:
            raise SystemExit(f"知识库不存在: {kb_id}")

        files = sorted(DEMO_DIR.glob("*.md"))
        print(f"发现 {len(files)} 个演示文档")
        for f in files:
            content = f.read_bytes()
            sha256 = hashlib.sha256(content).hexdigest()
            if doc_repo.get_by_sha256(kb_id, sha256) is not None:
                print(f"  ⏭️  跳过（已存在）: {f.name}")
                continue
            raw_text = content.decode("utf-8")
            doc = doc_repo.create(
                kb_id=kb_id,
                name=f.name,
                sha256=sha256,
                status="parsing",
                raw_text=raw_text,
            )
            try:
                import_document(doc.id, db)
                print(f"  ✅ 导入成功: {f.name}")
            except ImportError_ as e:
                print(f"  ❌ 导入失败: {f.name} → {e}")
                doc_repo.set_status(doc, "failed", str(e))
                db.commit()
        print("完成")
    finally:
        db.close()


if __name__ == "__main__":
    main()
