"""单文档导入（供 seed_demo_data 逐个进程调用，规避 torch 沙箱 segfault）。"""
import hashlib
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 本机直跑兜底（setdefault：已注入的容器/真实 env 优先）。密钥一律不进代码——
# 缺 ZHIPU_API_KEY 时由 llm_clients 报 ModelNotConfiguredError（fail-loud）。
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")

from app.core.database import SessionLocal
from app.repositories.document_repo import DocumentRepository
from app.services.knowledge_import_service import ImportError_, import_document


def main() -> None:
    file = Path(sys.argv[1])
    kb_id = uuid.UUID(sys.argv[2])
    db = SessionLocal()
    try:
        content = file.read_bytes()
        sha256 = hashlib.sha256(content).hexdigest()
        repo = DocumentRepository(db)
        if repo.get_by_sha256(kb_id, sha256) is not None:
            print(f"SKIP {file.name}")
            return
        doc = repo.create(
            kb_id=kb_id,
            name=file.name,
            sha256=sha256,
            status="parsing",
            raw_text=content.decode("utf-8"),
        )
        import_document(doc.id, db)
        print(f"OK {file.name}")
    except ImportError_ as e:
        print(f"FAIL {file.name} -> {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
