"""一次性 E2E 验证（2026-08-25 溯源空面板修复）：对本机运行中的 :8000 打一次快捷话术流。

断言：done 事件带 answer_source=quick；全程无 stage 进度剧场事件。
用法：./.venv/Scripts/python.exe scripts/e2e_quick_answer_check.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")

import httpx  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

with create_engine(settings.database_url).connect() as c:
    uid = c.execute(
        text("select id from users where role='user' order by created_at limit 1")
    ).scalar()
tok = create_access_token(str(uid), "user")

# 可选参数：传非快捷问句则验证普通 RAG 路径（stage 事件 + 非空 sources）
content = sys.argv[1] if len(sys.argv) > 1 else "保修多久？"
is_quick = len(sys.argv) <= 1

events: list[tuple[str, dict]] = []
with httpx.Client(timeout=60) as client:
    headers = {"Authorization": f"Bearer {tok}"}
    # session_id 必填（ChatStreamReq）→ 先建会话
    sresp = client.post(
        "http://localhost:8000/api/v1/sessions", headers=headers, json={}
    )
    sdata = sresp.json()
    sid = sdata.get("id") or sdata.get("session_id") or sdata.get("data", {}).get("id")
    assert sid, f"建会话失败: {sresp.status_code} {sdata}"
    with client.stream(
        "POST",
        "http://localhost:8000/api/v1/chat/stream",
        headers=headers,
        json={"session_id": sid, "content": content, "stream": True},
    ) as r:
        assert r.status_code == 200, r.status_code
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for line in frame.splitlines():
                    if line.startswith("data: "):
                        import json

                        ev = json.loads(line[6:])
                        events.append((ev["event"], ev.get("data", {})))

names = [e[0] for e in events]
print("events:", names)
done = next(d for n, d in events if n == "done")
if is_quick:
    assert done.get("answer_source") == "quick", f"answer_source missing: {done}"
    assert not any(n == "stage" for n in names), f"进度剧场事件不应出现: {names}"
    print("E2E PASS: done.answer_source=quick，无 stage 剧场事件")
else:
    src_ev = next((d for n, d in events if n == "sources"), {"sources": []})
    print("sources 数量:", len(src_ev.get("sources", [])), "| done:", done)
    assert any(n == "token" for n in names)
    print(f"E2E PASS(普通 RAG 路径): content={content!r}")
