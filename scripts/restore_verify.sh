#!/usr/bin/env bash
# 恢复演练 + 可恢复性验证（D3 / 审计 M4 2026-09-27）：把备份文件真的恢复成一座库并判定可用。
#
# 核心原则（本脚本存在的全部理由）：
#   **备份了没做恢复验证 = 没有备份。**
#   没有本脚本，backup_pg.sh 的产物只是一堆从未被证明能装回去的字节。
#
# 它验三件事（缺一项即 FAIL）：
#   1. 完整性：dump 的 sha256 与 .sha256 侧车文件对得上；
#   2. 结构可恢复：pg_restore --exit-on-error 全程零错误，且 schema 里必备表都在；
#   3. 版本对得上：恢复出来的库里 alembic_version == 代码侧 alembic head（比对，不假设）。
#   另附关键表 count(*) 抽样，写进 <dump>.verify.log 供两次演练横向比对。
#
# 安全约定（写死在逻辑里，不靠自觉）：
#   - 只往 --scratch-db 写：建库/删库/恢复全部指向它，源库连一次都不连（本脚本不调用任何
#     针对 --source-db 的连接命令，那个参数只用于「拒绝把备份恢复回它自己」这道闸门）；
#   - --scratch-db 必须看起来像一次性库（前缀 tmp_/verify_/restore_ 或后缀 _verify），
#     否则拒绝，除非显式 --force —— 防"手滑把生产库名当临时库填进来"这种一次性事故；
#   - 连接参数全部显式传入，本文件不含任何生产连接串；口令走 PGPASSWORD/~/.pgpass，不打印。
#
# 用法：
#   scripts/restore_verify.sh --dump bk/lingxi-20260927-000000.dump \
#     --source-db lingxi --scratch-db verify_lingxi_20260927 \
#     --host H --port 5432 --user U --backend-dir backend [--keep] [--dry-run]
#   # 无 alembic 可执行环境时用 --expected-rev 0023 替代 --backend-dir 的 head 查询
#
# 退出码：0 恢复验证通过 / 1 任一验证项失败 / 2 参数或依赖问题
set -euo pipefail

HOST="${PGHOST:-${POSTGRES_HOST:-}}"
PORT="${PGPORT:-${POSTGRES_PORT:-5432}}"
USER_="${PGUSER:-${POSTGRES_USER:-}}"
DUMP=""
SOURCE_DB=""
SCRATCH_DB=""
BACKEND_DIR=""
EXPECTED_REV=""
KEEP=0
FORCE=0
DRY=0

#: 恢复后必须存在的表（缺任一张即结构不完整）。与 app/models 的租户表集合一致。
REQUIRED_TABLES="users sessions messages message_sources knowledge_bases documents tickets feedback"

die() { printf '[FAIL] %s\n' "$*" >&2; exit "${2:-1}"; }
note() { printf '%s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dump)         DUMP="${2:-}"; shift 2 ;;
    --source-db)    SOURCE_DB="${2:-}"; shift 2 ;;
    --scratch-db)   SCRATCH_DB="${2:-}"; shift 2 ;;
    --host)         HOST="${2:-}"; shift 2 ;;
    --port)         PORT="${2:-}"; shift 2 ;;
    --user)         USER_="${2:-}"; shift 2 ;;
    --backend-dir)  BACKEND_DIR="${2:-}"; shift 2 ;;
    --expected-rev) EXPECTED_REV="${2:-}"; shift 2 ;;
    --keep)         KEEP=1; shift ;;
    --force)        FORCE=1; shift ;;
    --dry-run)      DRY=1; shift ;;
    -h|--help)      sed -n '2,27p' "$0"; exit 0 ;;
    *) die "未知参数：$1（--help 看用法）" 2 ;;
  esac
done

[ -n "$DUMP" ] || die "缺 --dump" 2
[ -f "$DUMP" ] || die "备份文件不存在：$DUMP" 2
[ -n "$SOURCE_DB" ] || die "缺 --source-db（用于「不得把备份恢复回它自己」这道闸门）" 2
[ -n "$SCRATCH_DB" ] || die "缺 --scratch-db（一次性目标库；本脚本唯一的写入对象）" 2
[ -n "$HOST" ] || die "缺 --host（或 PGHOST/POSTGRES_HOST）：不猜默认主机" 2
[ -n "$USER_" ] || die "缺 --user（或 PGUSER/POSTGRES_USER）" 2
case "$PORT" in (''|*[!0-9]*) die "--port 必须是数字，实际：$PORT" 2 ;; esac

# --- 闸门 1：目标库绝不能是源库 ---
[ "$SCRATCH_DB" != "$SOURCE_DB" ] || die "--scratch-db 不能等于 --source-db（$SOURCE_DB）：那会把备份盖回原库"

# --- 闸门 2：目标库必须像一次性库（防手滑填成生产库名）---
if [ "$FORCE" != 1 ]; then
  case "$SCRATCH_DB" in
    tmp_*|verify_*|restore_*|*_verify|*_tmp) : ;;
    *) die "--scratch-db=$SCRATCH_DB 不像一次性库名（需 tmp_/verify_/restore_ 前缀或 _verify 后缀）。确认要往它写入请加 --force" ;;
  esac
fi

sha_tool() {
  if command -v sha256sum >/dev/null 2>&1; then printf 'sha256sum'
  elif command -v shasum >/dev/null 2>&1; then printf 'shasum -a 256'
  else return 1; fi
}

for bin in pg_restore psql dropdb createdb; do
  command -v "$bin" >/dev/null 2>&1 || die "缺依赖 $bin" 2
done

PG=(--host="$HOST" --port="$PORT" --username="$USER_")
SQL() { psql "${PG[@]}" --dbname="$1" -X -q -Atc "$2"; }

# --- 期望版本号（alembic head）：先取 --expected-rev，否则从代码侧 alembic heads 取 ---
resolve_expected_rev() {
  if [ -n "$EXPECTED_REV" ]; then printf '%s' "$EXPECTED_REV"; return 0; fi
  [ -n "$BACKEND_DIR" ] || return 1
  command -v alembic >/dev/null 2>&1 || return 1
  # alembic heads 只读迁移目录，不连任何库（比对的是代码侧真图，不是字符串 grep）
  ( cd "$BACKEND_DIR" && alembic heads 2>/dev/null ) | awk 'NR==1{print $1}'
}

if [ "$DRY" = 1 ]; then
  note '[DRY-RUN] 不连库、不建库、不删库、不恢复。计划动作：'
  note "  校验哈希：$(sha_tool || echo 'sha256sum') -c $(basename "$DUMP").sha256  (于 $(dirname "$DUMP"))"
  note "  dropdb --if-exists ${PG[*]} $SCRATCH_DB"
  note "  createdb ${PG[*]} --template=template0 --encoding=UTF8 $SCRATCH_DB"
  note "  pg_restore ${PG[*]} --dbname=$SCRATCH_DB --no-owner --no-privileges --exit-on-error $DUMP"
  note "  SQL：SELECT version_num FROM alembic_version   → 期望 $(resolve_expected_rev 2>/dev/null || echo '<需 --expected-rev 或 --backend-dir+alembice可执行>')"
  note "  SQL：逐表 SELECT count(*) FROM $REQUIRED_TABLES"
  note "  写报告：${DUMP}.verify.log"
  [ "$KEEP" = 1 ] || note "  dropdb --if-exists ${PG[*]} $SCRATCH_DB   （一次性库用完即弃；--keep 保留）"
  note '[DRY-RUN] 未执行任何写操作。'
  exit 0
fi

REPORT="${DUMP}.verify.log"
LINE_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EXPECTED="$(resolve_expected_rev || true)"
[ -n "$EXPECTED" ] || die "无法确定期望 alembic head：给 --expected-rev 或 --backend-dir（且 alembic 可执行）"

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  printf '%s\t%s\tFAIL\t%s\n' "$LINE_START" "$(basename "$DUMP")" "$*" >> "$REPORT" 2>/dev/null || true
  exit 1
}

# --- 1. 完整性：产物哈希 ---
SIDE="${DUMP}.sha256"
# 无侧车=完整性未判定，此时给 PASS 是在谎报"这份备份能装回去"（头部契约：缺一项即 FAIL）
[ -f "$SIDE" ] || fail "缺 .sha256 侧车文件（$SIDE）：完整性无从判定，请改用 backup_pg.sh 的产物或补记哈希"
SHAS="$(sha_tool)" || die "缺 sha256sum/shasum，无法校验产物哈希" 2
( cd "$(dirname "$DUMP")" && $SHAS -c "$(basename "$SIDE")" >/dev/null ) \
  || fail "sha256 校验不匹配：备份文件已损坏或被改动，不可用于恢复"
note '[OK] 1/3 哈希完整性通过'

# --- 2. 建一次性库并恢复（唯一写入对象 = $SCRATCH_DB）---
note "→ 丢弃并重建一次性库 $SCRATCH_DB（源库 $SOURCE_DB 全程不连接）"
dropdb --if-exists "${PG[@]}" "$SCRATCH_DB" || fail "dropdb $SCRATCH_DB 失败"
createdb "${PG[@]}" --template=template0 --encoding=UTF8 "$SCRATCH_DB" \
  || fail "createdb $SCRATCH_DB 失败"
pg_restore "${PG[@]}" --dbname="$SCRATCH_DB" --no-owner --no-privileges --exit-on-error "$DUMP" \
  || fail "pg_restore 到 $SCRATCH_DB 报错（--exit-on-error）：备份不可用于恢复"
note '[OK] 2/3 结构恢复零错误'

# --- 3. 版本比对 + 关键表 count(*) 抽样 ---
ACTUAL="$(SQL "$SCRATCH_DB" "SELECT version_num FROM alembic_version" 2>/dev/null | head -n1 || true)"
[ -n "$ACTUAL" ] || fail "恢复出的库里没有 alembic_version（或为空）——无法判定版本"
[ "$ACTUAL" = "$EXPECTED" ] || fail "alembic 版本不符：恢复库=$ACTUAL 代码 head=$EXPECTED（备份落后于代码或迁移链断裂）"
note "[OK] 3/3 alembic 版本对齐：$ACTUAL"

COUNTS=""
for t in $REQUIRED_TABLES; do
  exists="$(SQL "$SCRATCH_DB" "SELECT 1 FROM information_schema.tables WHERE table_name='$t' LIMIT 1" || true)"
  [ -n "$exists" ] || fail "必备表缺失：$t"
  n="$(SQL "$SCRATCH_DB" "SELECT count(*) FROM $t" || true)"
  [ -n "$n" ] || fail "count(*) 读取失败：$t"
  COUNTS="${COUNTS}  $t=${n}"
done
note "[OK] 必备表齐全，行数抽样：$COUNTS"

{
  printf '%s\t%s\tPASS\talembic=%s\t%s\n' "$LINE_START" "$(basename "$DUMP")" "$ACTUAL" "$COUNTS"
} >> "$REPORT"

if [ "$KEEP" != 1 ]; then
  dropdb --if-exists "${PG[@]}" "$SCRATCH_DB" || note '[WARN] 一次性库清理失败（不影响验证结论），请手工 dropdb'
fi

note "[PASS] 恢复演练通过：$(basename "$DUMP") → $SCRATCH_DB → 已判定可用"
note "       报告：$REPORT"
note "       注意：本结论只对这一个文件成立。备份要算数，得周期性重跑本脚本（有备份的定义是"
note "       「随时能装回去并验证过」），而不是「某天一早在跑」。"
