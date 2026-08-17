"""灵犀 M5 端到端冒烟：health → 注册 → 登录 → me → 建会话 → SSE 问答。"""
import json
import os
import sys
import uuid
from pathlib import Path

# 步骤 9 注入缓存需 import app 模块（backend 包）；宿主直连容器 Redis/Qdrant/PG
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
PASS = "smoke-pass-123"


def main() -> int:
    fails = []
    with httpx.Client(base_url=BASE, timeout=httpx.Timeout(10.0, read=90.0)) as c:
        # 1) health（根路径，非 /api/v1 前缀）
        r = httpx.get("http://127.0.0.1:8000/health", timeout=5)
        assert r.status_code == 200 and r.json().get("status") == "ok", r.text
        print("[1] /health OK")

        # 2) register
        email = f"smoke-{uuid.uuid4().hex[:8]}@lingxi.test"
        r = c.post("/auth/register", json={"email": email, "password": PASS})
        assert r.status_code == 201, f"register {r.status_code}: {r.text[:300]}"
        data = r.json()
        assert data.get("access_token") and data.get("refresh_token"), data
        token = data["access_token"]
        user_id = data["user_id"]
        print(f"[2] register OK user={user_id[:8]}")

        # 3) login
        r = c.post("/auth/login", json={"account": email, "password": PASS})
        assert r.status_code == 200, f"login {r.status_code}: {r.text[:300]}"
        print("[3] login OK")

        # 4) me
        r = c.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, f"me {r.status_code}: {r.text[:300]}"
        me = r.json()
        assert me.get("email") == email and me.get("role") == "user", me
        print(f"[4] me OK role={me.get('role')} quota_left={me.get('quota_left')}")

        # 5) create session
        r = c.post("/sessions", json={"title": "M5 冒烟"}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, f"create_session {r.status_code}: {r.text[:300]}"
        sid = r.json()["session_id"]
        print(f"[5] create_session OK sid={sid[:8]}")

        # 6) SSE chat stream（真实 RAG 管线；无 KB 时观察错误契约而非崩溃）
        with c.stream(
            "POST",
            "/chat/stream",
            json={"session_id": sid, "content": "你好，介绍一下你们", "stream": True},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            assert resp.status_code == 200, f"chat_stream http {resp.status_code}"
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        pass
        kinds = [e.get("event") for e in events]
        print(f"[6] chat_stream events={kinds}")
        if kinds:
            last = events[-1]
            if last.get("event") == "done":
                print(f"    done message_id={last.get('data', {}).get('message_id', '')[:8]}")
            elif last.get("event") == "error":
                print(f"    error={last.get('data', {}).get('code')} ({last.get('data', {}).get('message', '')[:80]})")
            # R-2：intent 事件为首事件，携带真实意图
            assert kinds[0] == "intent", f"首事件应为 intent，实际 {kinds[:2]}"
            assert events[0]["data"]["intent"] in ("qa", "handoff", "chitchat"), events[0]
        else:
            print("    (无事件——视为异常)")
            fails.append("chat_stream 无 SSE 事件")

        # 7) R-4：refresh 轮换 —— 新 refresh 可用，旧 refresh 复用应 401
        r = c.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert r.status_code == 200, f"refresh {r.status_code}: {r.text[:300]}"
        new_refresh = r.json().get("refresh_token")
        assert new_refresh and new_refresh != data["refresh_token"], "R-4: 应返回轮换后的新 refresh"
        r2 = c.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
        assert r2.status_code == 401, f"R-4: 旧 refresh 应失效，实际 {r2.status_code}"
        print("[7] refresh rotation OK (旧 token 复用 401)")

        # 8) T1：handoff → AI 建单链路（真实意图分流 + done 带工单号）
        r = c.post("/sessions", json={"title": "M5 冒烟·工单"}, headers={"Authorization": f"Bearer {token}"})
        sid2 = r.json()["session_id"]
        with c.stream(
            "POST",
            "/chat/stream",
            json={"session_id": sid2, "content": "我要投诉找经理", "stream": True},
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            assert resp.status_code == 200
            evs = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    try:
                        evs.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        pass
        kinds2 = [e.get("event") for e in evs]
        intent_ev = next((e for e in evs if e.get("event") == "intent"), None)
        done_ev = next((e for e in evs if e.get("event") == "done"), None)
        assert intent_ev and intent_ev["data"]["intent"] == "handoff", f"应判 handoff: {kinds2}"
        assert done_ev and done_ev["data"].get("ticket_id"), f"done 应带 ticket_id: {done_ev}"
        print(f"[8] T1 handoff→建单 OK (intent=handoff, ticket_id={done_ev['data']['ticket_id'][:8]})")

        # 9) T10：答案缓存命中链路（注入预置缓存，绕开 LLM 403——机制验证；内容质量待 key 修复后补）
        def _ask(content):
            with c.stream(
                "POST",
                "/chat/stream",
                json={"session_id": sid, "content": content, "stream": True},
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                evs = []
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        try:
                            evs.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass
            return evs

        # 9) T10：真实缓存闭环（同问两次 → 第一次生成回填 → 第二次命中 cache_hit）
        q9 = "退货后多久能收到退款"  # 全新问题（确保第一次 miss → 回填 → 第二次命中）
        ev_a = _ask(q9)
        ev_b = _ask(q9)
        done_a = next((e for e in ev_a if e.get("event") == "done"), None)
        done_b = next((e for e in ev_b if e.get("event") == "done"), None)
        hit_a = bool(done_a and done_a["data"].get("cache_hit"))
        hit_b = bool(done_b and done_b["data"].get("cache_hit"))
        assert not hit_a, f"第一次不应命中缓存: {[e.get('event') for e in ev_a]}"
        assert hit_b, f"T10: 第二次同问应缓存命中，events={[e.get('event') for e in ev_b]}"
        print(f"[9] T10 缓存闭环 OK (第一次 cache_hit={hit_a}, 第二次 cache_hit={hit_b})")

    if fails:
        print("SMOKE_FAIL:", fails)
        return 1
    print("SMOKE_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
