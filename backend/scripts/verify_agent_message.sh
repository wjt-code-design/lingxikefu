#!/usr/bin/env bash
# ============================================================================
# Branch 3 一键端到端验证：人工客服消息落库 + 顾客端可见
# 场景：客服向顾客会话代发 role=agent 消息 → 顾客拉详情能看到（前端 3s 轮询同源）。
#
# 前置条件（务必先满足，否则必然失败）：
#   1. 已执行迁移：cd backend && alembic upgrade head（alembic current 应显示 0009）
#   2. 后端已启动（uvicorn app.main:app 或 start_dev.bat）
#   3. 两个可用账号：客服（admin/agent）+ 顾客（user）
#
# 用法（环境变量传参）：
#   BASE_URL=http://localhost:8200/api/v1 \
#   AGENT_ACCOUNT=客服账号 AGENT_PASSWORD=客服密码 \
#   CUSTOMER_ACCOUNT=顾客账号 CUSTOMER_PASSWORD=顾客密码 \
#   bash scripts/verify_agent_message.sh
#
# 可选：
#   SESSION_ID=xxx     复用已有会话（默认：顾客新建一个空会话）
#   CONTENT=xxx        代发内容（默认一条标准话术）
#
# 判断标准：输出 PASS 即端到端闭环；FAIL 会给出排查方向。
# ============================================================================
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000/api/v1}"
AGENT_ACCOUNT="${AGENT_ACCOUNT:?需要 AGENT_ACCOUNT（客服账号）}"
AGENT_PASSWORD="${AGENT_PASSWORD:?需要 AGENT_PASSWORD}"
CUSTOMER_ACCOUNT="${CUSTOMER_ACCOUNT:?需要 CUSTOMER_ACCOUNT（顾客账号）}"
CUSTOMER_PASSWORD="${CUSTOMER_PASSWORD:?需要 CUSTOMER_PASSWORD}"
SESSION_ID="${SESSION_ID:-}"
CONTENT="${CONTENT:-您好，我是人工客服，已收到您的问题，正在为您处理。}"

PY=python

# JSON 取值：echo '<json>' | jget <key>
jget() {
  "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(d.get(sys.argv[1], ""))' "$1"
}

fail() { echo "FAIL: $1"; exit 1; }

echo "==> 1/4 客服登录（$AGENT_ACCOUNT）"
AGENT_TOKEN=$(curl -sf -X POST "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"account\":\"$AGENT_ACCOUNT\",\"password\":\"$AGENT_PASSWORD\"}" | jget access_token)
[ -n "$AGENT_TOKEN" ] || fail "客服登录失败（账号/密码/后端未启动？）"

echo "==> 2/4 顾客登录（$CUSTOMER_ACCOUNT）"
CUST_TOKEN=$(curl -sf -X POST "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"account\":\"$CUSTOMER_ACCOUNT\",\"password\":\"$CUSTOMER_PASSWORD\"}" | jget access_token)
[ -n "$CUST_TOKEN" ] || fail "顾客登录失败"

echo "==> 3/4 准备会话"
if [ -n "$SESSION_ID" ]; then
  SID="$SESSION_ID"
  echo "   复用会话 $SID"
else
  SID=$(curl -sf -X POST "$BASE_URL/sessions" \
    -H "Authorization: Bearer $CUST_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"title":"verify-agent-message"}' | jget session_id)
  [ -n "$SID" ] || fail "顾客创建会话失败"
  echo "   顾客新建会话 $SID"
fi

echo "==> 4/4 客服代发 → 顾客校验"
RESP=$(curl -sf -X POST "$BASE_URL/sessions/$SID/messages" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$(printf '{"content":"%s"}' "$CONTENT")") \
  || fail "代发请求失败（是否已执行 alembic upgrade head？enum 缺 agent 会报 500/428）"
MSG_ID=$(echo "$RESP" | jget id)
AGENT_NAME=$(echo "$RESP" | jget agent_name)
[ -n "$MSG_ID" ] || fail "代发响应缺少 id（响应: $RESP）"

DETAIL=$(curl -sf "$BASE_URL/sessions/$SID" -H "Authorization: Bearer $CUST_TOKEN")
VISIBLE=$(echo "$DETAIL" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
hit = [m for m in d.get("messages", [])
       if m.get("role") == "agent" and m.get("content") == sys.argv[1] and m.get("agent_name")]
print("1" if hit else "0")
' "$CONTENT")
if [ "$VISIBLE" = "1" ]; then
  echo "PASS: 端到端闭环 —— 顾客端可见 role=agent 消息 id=$MSG_ID agent_name=$AGENT_NAME"
  echo "      前端顾客页应在 3 秒轮询内自动出现该气泡；刷新会话也不丢。"
else
  fail "顾客端详情未看到 agent 消息（检查 get_session 是否透出 agent 字段 / 迁移是否应用）"
fi
