"""灌入模拟真实场景的演示数据到知识库（物流/退款/送装/保修/配送）。

用法：python scripts/seed_demo_data.py [kb_id]
不传 kb_id 则用当前租户最新创建的 KB（与 chat 路由一致）。
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
    kb = db.scalar(
        sqlalchemy.select(KnowledgeBase)
        .where(KnowledgeBase.tenant_id == settings.TENANT_DEFAULT)
        .order_by(KnowledgeBase.created_at.desc())
        .limit(1)
    )
    if kb is not None:
        return kb.id
    # CI/全新环境：无知识库时不退出，自动创建 demo 库（订单集成回归 seed 自给自足）。
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
