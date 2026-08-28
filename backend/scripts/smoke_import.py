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

import argparse
import hashlib
import sys
from pathlib import Path

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.knowledge import Chunk, Document, DocumentStatus
from app.repositories.document_repo import DocumentRepository, KnowledgeBaseRepository
from app.services.document_service import extract_text
from app.services.knowledge_import_service import ImportError_, import_document


def check_doc_set(kb_docs: set[str], source_files: set[str]) -> list[str]:
    """KB 内文档名 vs kb/ 源文件名差异审计，返回 KB 多出的文档名（污染嫌疑）。

    2026-08-28 事故：seed_demo_data 无参运行曾把 9 个演示文档混入评测库，
    检索分布漂移致本地/CI 口径分裂、Q042 缺陷假绿（BASELINE §五）。此审计防复发。
    """
    return sorted(kb_docs - source_files)

BASE = Path(__file__).resolve().parent.parent.parent / "eval-and-samples"
KB_DIR = BASE / "kb"
PDF_DIR = BASE / "kb-pdf"

#: 冒烟库语义化分类名 = 评测/匹配的 KB 口径唯一真源（eval_faithfulness/eval_recall 引用，须一致）
_KB_NAME = "星河智家·官方政策库"


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


def _skip_or_repair(existing, db, kb_id) -> bool:
    """幂等命中后是否可直接 SKIP（返回 True=向量完整可跳过）。

    兜底两条（2026-08-27 实测事故）：
    - indexed 但向量不完整（部分 chunk 写入失败）→ 需 REPAIR；
    - status 非 indexed（failed stub，chunks=0/vec=0 会被完整性误判"齐"）→ 需 REPAIR。
    """
    if existing.status != DocumentStatus.indexed:
        return False
    return _doc_vector_integrity(db, kb_id, existing.id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true",
                        help="审计出非 kb/ 源文档时 exit 1（CI 用）；默认仅告警")
    args = parser.parse_args()

    db = SessionLocal()
    kb_repo = KnowledgeBaseRepository(db)
    doc_repo = DocumentRepository(db)

    # 幂等：按名复用已有同名库，不重复新建（修复"多次运行堆同名冒烟库"）。
    # 命名去「冒烟库」字眼，改语义化分类名（_KB_NAME 为模块级单一真源）。
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
        if existing and _skip_or_repair(existing, db, kb.id):
            print(f"  [SKIP] {f.name} (sha256 重复)")
            skipped += 1
            continue
        if existing:
            # 2026-08-27 历史事故兜底：首次导入部分向量缺失而 doc 仍 indexed，
            # 幂等 skip 会让缺向量永远补不齐 → 检出后强制重导（import_document 幂等清残向量）。
            # 非 indexed（failed）一并重导：failed doc chunks=0/vec=0 会被完整性误判为"齐"，
            # 修复前会被 SKIP 挡住，自愈被 stub doc 卡死（实测：REPAIR 重导失败留下 failed stub）。
            reason = "status 非 indexed" if existing.status != DocumentStatus.indexed else "向量不完整"
            print(f"  [REPAIR] {f.name} 幂等命中但{reason}，强制重导")
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

    # 文档清单审计：KB 内文档名必须全部来自 kb/（+kb-pdf/）源目录。
    # 2026-08-28 事故：seed_demo_data 污染评测库致本地/CI 检索分布分裂——此防线防复发。
    src_names = {f.name for f in files}
    kb_doc_names = {d.name for d in db.query(Document).filter_by(kb_id=kb.id).all()}
    extra = check_doc_set(kb_doc_names, src_names)
    if extra:
        msg = (f"KB 含 {len(extra)} 个非 kb/ 源文档（疑似 seed_demo_data 污染）: {extra}；"
               "清理方法见 BASELINE.md §五")
        if args.strict:
            print(f"[GUARD][FAIL] {msg}")
            sys.exit(1)
        print(f"[GUARD][WARN] {msg}")

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
