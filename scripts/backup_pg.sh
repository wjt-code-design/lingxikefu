#!/usr/bin/env bash
# PG 逻辑备份（D3 / 审计 M4 2026-09-27）：pg_dump -Fc 产物 + sha256 留档 + 结构自检。
#
# 为什么要有这个文件：全仓此前没有任何备份/恢复载体——「回滚」只有 alembic downgrade
# （CI 从未跑过全链，只验过 1 步）和 KB 批次回滚（业务面，不是数据面）两条路。
#
# 核心原则（写给下一个读它的人，别改成"顺手备份就行"）：
#   **备份了没做恢复验证 = 没有备份。**
#   本脚本只产出「未证实可用的文件」；可用性判定在 scripts/restore_verify.sh。
#   一次真实演练 = backup_pg.sh 跑一次 + restore_verify.sh 对同一文件跑通并 PASS。
#
# 安全约定：
#   - 连接参数全部显式传入（或来自 POSTGRES_* 环境变量），本文件不含任何生产连接串；
#   - 口令不落在参数里（走 PGPASSWORD / ~/.pgpass），脚本全程不打印它；
#   - 对源库只执行 pg_dump（只读），不写任何对象、不加锁表（--no-sync 之外的写操作为零）。
#
# 用法：
#   scripts/backup_pg.sh --host H --port 5432 --user U --dbname DB --outdir DIR [--outfile NAME] [--dry-run]
#   scripts/backup_pg.sh --outdir /var/backups/lingxi --dry-run     # 主机/库名从 POSTGRES_* 环境变量取
#   scripts/backup_pg.sh --dbname postgres --outdir ./bk --host "$PGHOST" --dry-run
#
# 退出码：0 成功 / 1 参数或校验失败 / 2 依赖缺失
set -euo pipefail

HOST="${POSTGRES_HOST:-}"
PORT="${POSTGRES_PORT:-5432}"
USER_="${POSTGRES_USER:-}"
DBNAME="${POSTGRES_DB:-}"
OUTDIR=""
OUTFILE=""
DRY=0

die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --host)    HOST="${2:-}"; shift 2 ;;
    --port)    PORT="${2:-}"; shift 2 ;;
    --user)    USER_="${2:-}"; shift 2 ;;
    --dbname)  DBNAME="${2:-}"; shift 2 ;;
    --outdir)  OUTDIR="${2:-}"; shift 2 ;;
    --outfile) OUTFILE="${2:-}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) die "未知参数：$1（--help 看用法）" ;;
  esac
done

[ -n "$HOST" ]   || die "缺 --host（或环境变量 POSTGRES_HOST）：不猜默认主机，防误连他库"
[ -n "$USER_" ]  || die "缺 --user（或环境变量 POSTGRES_USER）"
[ -n "$DBNAME" ] || die "缺 --dbname（或环境变量 POSTGRES_DB）：不猜默认库名，防备份错对象"
[ -n "$OUTDIR" ] || die "缺 --outdir"
case "$PORT" in (*[!0-9]*|'') die "--port 必须是数字，实际：$PORT" ;; esac

STAMP="$(date -u +%Y%m%d-%H%M%SZ)"
[ -n "$OUTFILE" ] || OUTFILE="${DBNAME}-${STAMP}.dump"
DUMP="${OUTDIR}/${OUTFILE}"
DUMP_PART="${DUMP}.part"  # 先写临时名，成功才 mv 占位
SIDE="${DUMP}.sha256"

sha_tool() {
  if command -v sha256sum >/dev/null 2>&1; then printf 'sha256sum'
  elif command -v shasum >/dev/null 2>&1; then printf 'shasum -a 256'
  else return 1; fi
}

for bin in pg_dump pg_restore psql; do
  command -v "$bin" >/dev/null 2>&1 || { printf '[FAIL] 缺依赖 %s\n' "$bin" >&2; exit 2; }
done

DUMP_CMD=(pg_dump --format=custom --no-password --file="$DUMP_PART"
          --host="$HOST" --port="$PORT" --username="$USER_" --dbname="$DBNAME")

if [ "$DRY" = 1 ]; then
  printf '[DRY-RUN] 不执行、不写文件。计划动作：\n'
  printf '  mkdir -p %q\n' "$OUTDIR"
  printf '  %q' "${DUMP_CMD[@]}"; printf '\n'
  printf '  mv -f %q %q   # dump 成功才占位，失败运行不覆盖上一份好备份\n' "$DUMP_PART" "$DUMP"
  printf '  %s %s > %s   # 记录产物哈希\n' "$(sha_tool || echo 'sha256sum')" "$DUMP" "$SIDE"
  printf '  pg_restore --list %s > /dev/null   # 结构自检（不连任何库）\n' "$DUMP"
  printf '  追加一行到 %s/MANIFEST.txt\n' "$OUTDIR"
  printf '[DRY-RUN] 下一步（真正验证可用性，必须跑）：\n'
  printf '  scripts/restore_verify.sh --dump %s --source-db %s --scratch-db <一次性库名> --host %s --port %s --user %s\n' \
    "$DUMP" "$DBNAME" "$HOST" "$PORT" "$USER_"
  exit 0
fi

SHAS="$(sha_tool)" || die "既无 sha256sum 也无 shasum，无法留档哈希"

mkdir -p "$OUTDIR"
trap 'rm -f "$DUMP_PART"' EXIT
"${DUMP_CMD[@]}"
[ -s "$DUMP_PART" ] || die "pg_dump 产物为空：$DUMP_PART（未备份成功）"
mv -f "$DUMP_PART" "$DUMP"

( cd "$OUTDIR" && $SHAS "$(basename "$DUMP")" > "$(basename "$SIDE")" )
# 立即自校一次：连「备份文件本身损坏/被截断」这一层都不留到恢复日
( cd "$OUTDIR" && $SHAS -c "$(basename "$SIDE")" >/dev/null )
pg_restore --list "$DUMP" >/dev/null || die "pg_restore --list 读不出目录：产物结构不可用 $DUMP"
ENTRIES="$(pg_restore --list "$DUMP" | grep -c '^[0-9][0-9]*;' || true)"

printf '%s\t%s\t%s bytes\tdump-entries=%s\n' "$STAMP" "$DBNAME" "$(wc -c < "$DUMP" | tr -d ' ')" "$ENTRIES" \
  >> "${OUTDIR}/MANIFEST.txt"

printf '[OK] 备份完成（但尚未经恢复验证，按定义还不算"有备份"）\n'
printf '  产物：%s\n  哈希：%s\n' "$DUMP" "$SIDE"
printf '  下一步：scripts/restore_verify.sh --dump %s --source-db %s --scratch-db <一次性库名> --host %s --port %s --user %s --backend-dir backend\n' \
  "$DUMP" "$DBNAME" "$HOST" "$PORT" "$USER_"
