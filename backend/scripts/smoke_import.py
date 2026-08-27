"""BU-04 真导入冒烟：真实 PG + 真实 Qdrant + 真实 bge 全链路。

用法（backend/ 下，需先 docker compose up postgres redis qdrant + migrate）：
    python -m scripts.smoke_import

验证点：
1. kb/ 13 篇文档全部导入成功（status=indexed）；kb-pdf/ 存在时一并导入其中的 PDF（可选）；
2. 重复上传同内容 → sha256 去重跳过（不重复导入）；
3. PDF 解析成功（chunk_count>0，修 AegisDesk 坑；kb-pdf 缺失时跳过并告警，不阻塞）；
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


def _count_doc_points(client, collection: str, kb_id, doc_id) -> int:
    """Qdrant 中该文档（kb_id+doc_id 过滤）的向量点数。"""
    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    cnt = client.count(
        collection_name=collection,
        exact=True,
        count_filter=Filter(
            must=[
                FieldCondition(key="kb_id", match=MatchValue(value=str(kb_id))),
                FieldCondition(key="doc_id", match=MatchValue(value=str(doc_id))),
            ]
        ),
    )
    return cnt.count


def _doc_vector_integrity(db, kb_id, doc_id) -> bool:
    """该文档 Qdrant 向量数 == PG chunks 数。

    历史事故（2026-08-27 实测）：首次导入时部分 chunk 向量写入失败而文档仍标 indexed，
    随后幂等 skip（只查 sha256）让缺向量永远补不齐 → 检索缺料。幂等命中前的完整性
    校验即为此兜底（集合名经 get_collection_name 随 RAG_ENABLE_HYBRID 动态解析）。
    """
    from app.services.vector_service import get_collection_name
    from qdrant_client import QdrantClient

    pg = db.query(Chunk).filter_by(kb_id=kb_id, doc_id=doc_id).count()
    points = _count_doc_points(
        QdrantClient(url=settings.QDRANT_URL), get_collection_name(), kb_id, doc_id
    )
    return points == pg


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

    files = sorted(KB_DIR.iterdir())
    if PDF_DIR.is_dir():
        files += sorted(PDF_DIR.iterdir())
    else:
        print(f"[WARN] kb-pdf 目录不存在（{PDF_DIR}），跳过 PDF 导入（可选验证）")
    print(f"[INPUT] {len(files)} files")

    imported, skipped, failed = 0, 0, 0
    for f in files:
        content = f.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        existing = doc_repo.get_by_sha256(kb.id, sha)
        if existing:
            if _doc_vector_integrity(db, kb.id, existing.id):
                print(f"  [SKIP] {f.name} (sha256 重复)")
                skipped += 1
                continue
            # 2026-08-27 历史事故兜底：首次导入部分向量缺失而 doc 仍 indexed，
            # 幂等 skip 会让缺向量永远补不齐 → 检出后删记录强制重导（import_document 幂等清残向量）。
            print(f"  [REPAIR] {f.name} 幂等命中但向量不完整，强制重导")
            db.query(Chunk).filter_by(kb_id=kb.id, doc_id=existing.id).delete(synchronize_session=False)
            db.delete(existing)
            db.commit()
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
        # Bug B（2026-08-27 修复）：计数集合必须与导入写入一致（get_collection_name，
        # 受 RAG_ENABLE_HYBRID 控制）——此前硬编码 QDRANT_COLLECTION 在 hybrid 模式
        # 下 count 旧集合 → 404 → return 2，eval 导入被误判失败
        from app.services.vector_service import get_collection_name
        from qdrant_client import QdrantClient
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        client = QdrantClient(url=settings.QDRANT_URL)
        count = client.count(
            collection_name=get_collection_name(),
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
