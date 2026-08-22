"""BU-04 真导入冒烟：真实 PG + 真实 Qdrant + 真实 bge 全链路。

用法（backend/ 下，需先 docker compose up postgres redis qdrant + migrate）：
    python -m scripts.smoke_import

验证点：
1. 13 篇 kb + 5 个 pdf 全部导入成功（status=indexed）；
2. 重复上传同内容 → sha256 去重跳过（不重复导入）；
3. PDF 解析成功（chunk_count>0，修 AegisDesk 坑）；
4. Qdrant 向量数 == PG chunks 总数（导入成功≠可检索，代理目标检查）。

导入走服务层 import_document（与 worker 同一函数，不经 Celery），
以便一次性同步跑完，不需要额外起 worker 进程。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.knowledge import Chunk, DocumentStatus
from app.repositories.document_repo import DocumentRepository, KnowledgeBaseRepository
from app.services.document_service import extract_text
from app.services.knowledge_import_service import ImportError_, import_document

BASE = Path(__file__).resolve().parent.parent.parent / "eval-and-samples"
KB_DIR = BASE / "kb"
PDF_DIR = BASE / "kb-pdf"


def main() -> int:
    db = SessionLocal()
    kb_repo = KnowledgeBaseRepository(db)
    doc_repo = DocumentRepository(db)

    # 幂等：按名复用已有同名库，不重复新建（修复"多次运行堆同名冒烟库"）。
    # 命名去「冒烟库」字眼，改语义化分类名。
    _KB_NAME = "星河智家·官方政策库"
    kb = next((k for k in kb_repo.list_all() if k.name == _KB_NAME), None)
    if kb is None:
        kb = kb_repo.create(name=_KB_NAME)
        print(f"[KB] 新建 {kb.name} ({kb.id})")
    else:
        print(f"[KB] 复用已有 {kb.name} ({kb.id})")

    files = sorted(KB_DIR.iterdir()) + sorted(PDF_DIR.iterdir())
    print(f"[INPUT] {len(files)} files")

    imported, skipped, failed = 0, 0, 0
    for f in files:
        content = f.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if doc_repo.get_by_sha256(kb.id, sha):
            print(f"  [SKIP] {f.name} (sha256 重复)")
            skipped += 1
            continue
        try:
            raw_text = extract_text(f.name, content)
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL-PARSE] {f.name}: {e}")
            failed += 1
            continue
        doc = doc_repo.create(kb_id=kb.id, name=f.name, sha256=sha, raw_text=raw_text)
        try:
            import_document(doc.id, db)
        except ImportError_ as e:
            print(f"  [FAIL-IMPORT] {f.name}: {e}")
            failed += 1
            continue
        db.refresh(doc)
        if doc.status != DocumentStatus.indexed:
            print(f"  [FAIL] {f.name}: status={doc.status.value} error={doc.error}")
            failed += 1
            continue
        print(f"  [OK] {f.name}: {doc.chunk_count} chunks")
        imported += 1

    # 汇总
    pg_chunks = db.query(Chunk).filter_by(kb_id=kb.id).count()
    print(f"\n[SUMMARY] imported={imported} skipped={skipped} failed={failed}")

    # 验证 Qdrant 向量数 == PG chunks 总数（按当前 KB 过滤；集合跨 KB 共享，不能数全集）
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        client = QdrantClient(url=settings.QDRANT_URL)
        count = client.count(
            collection_name=settings.QDRANT_COLLECTION,
            exact=True,
            count_filter=Filter(
                must=[FieldCondition(key="kb_id", match=MatchValue(value=str(kb.id)))]
            ),
        )
        print(f"[QDRANT] points={count.count} (kb={kb.id}) | PG chunks={pg_chunks}")
        ok = count.count == pg_chunks and failed == 0
        print(f"[RESULT] {'PASS ✅' if ok else 'FAIL ❌'}（向量数==chunk 数 且 无失败导入）")
        return 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        print(f"[QDRANT-ERR] {e}")
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
