#!/usr/bin/env python
"""重建 hybrid 检索索引：对指定 KB 的文档重新导入（切分+embedding+写 hybrid 集合）。

用途（ADR-2026-08-16 P1）：hybrid 集合 lingxi_hybrid_bge_768 首次启用时，
把 f3d26c91（23 份）文档重导一次，生成 dense+sparse 双路索引。
纯 dense 旧集合 lingxi_bge_768 不动（回滚路径：RAG_ENABLE_HYBRID=false 即用旧索引）。

用法：QDRANT_URL=http://localhost:6333 python scripts/rebuild_hybrid_index.py [KB_ID]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("POSTGRES_DB", "lingxi")
os.environ.setdefault("CHAT_PROVIDER", "zhipu")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models.knowledge import Document, KnowledgeBase  # noqa: E402
from app.services.knowledge_import_service import import_document  # noqa: E402

DEFAULT_KB = "f3d26c91-cb3e-42b6-8f19-9610e662976e"


def main() -> int:
    kb_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_KB
    pg = create_engine(os.environ.get("PG_URL") or "postgresql+psycopg://lingxi:__CHANGE_ME__@localhost:5432/lingxi")
    with sessionmaker(bind=pg)() as db:
        kb = db.get(KnowledgeBase, UUID(kb_id))
        if kb is None:
            print(f"KB 不存在: {kb_id}")
            return 1
        doc_ids = db.scalars(select(Document.id).where(Document.kb_id == UUID(kb_id))).all()
    print(f"重导 KB {kb_id} 的 {len(doc_ids)} 份文档到 hybrid 集合…")
    t0 = time.time()
    ok = fail = 0
    for i, did in enumerate(doc_ids, 1):
        try:
            with sessionmaker(bind=pg)() as db:
                doc = import_document(did, db)
            ok += 1
            print(f"  [{i}/{len(doc_ids)}] {doc.name} → {doc.status.value} ({time.time()-t0:.0f}s)")
        except Exception as e:  # noqa: BLE001
            fail += 1
            print(f"  [{i}/{len(doc_ids)}] 失败: {did} → {str(e)[:100]}")
    print(f"\n完成: {ok} 成功, {fail} 失败, 总耗时 {time.time()-t0:.0f}s")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
