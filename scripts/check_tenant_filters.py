"""红线⑨ 静态尺子（A6 / 审计 M4 2026-09-27）：带 tenant_id 的模型，其 select() 必须显式按租户过滤。

背景：红线⑨ 声明「全表 tenant_id 需显式过滤」，但此前唯一守着它的 tests/test_models_tenant.py
只验 schema 形状（列在不在、是不是第一个非主键列、有没有索引），**不验任何一条查询**——于是
app/api/eval.py 与 app/api/audit_logs.py 整文件零 tenant（grep -c tenant = 0）照样全仓全绿。

手法（纯标准库 AST，与 scripts/check_baseline_hashes.py 同族的「免费面」尺子）：
1. 从 app/models/*.py 动态收集「显式声明了 tenant_id 列」的模型类名 —— 新建带租户的表
   自动进入射程，不需要改本脚本（这是它比硬编码名单强的地方）；
2. 扫 backend/app/api/*.py + backend/app/services/*.py，找 `select(<模型>[ 或 .列])` 调用，
   取其**所在语句**的源码；
3. 语句里出现 `tenant_id` 即通过；否则做一跳**条件列表溯源**（`where(*cond)` /
   `cond = _visibility_cond(...)` 是本仓通用写法，条件在同名赋值或同文件辅助函数里时
   同样算已过滤，删掉那行 tenant 条件尺子照样红）；再不过就必须登记进 EXEMPT
   （按 `相对路径::函数限定名::模型名` 锚定，不用行号——行号一改就漂）。EXEMPT 只收
   「归属校验/上游已过滤后按主键取数」那一类，每条必须各写一句为什么；条目失配
   （函数被改名/删除）同样判失败，防白名单腐化。

退出码：0 = 全部通过；1 = 有未登记的漏网查询 或 有失效的豁免条目（fail-closed）。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "backend" / "app" / "models"
SCAN_DIRS = (
    ROOT / "backend" / "app" / "api",
    ROOT / "backend" / "app" / "services",
)

#: 豁免清单：`相对路径::函数限定名::模型名` → 为什么这一条不需要（且不应该）带 tenant 条件。
#: 准入标准只有两类：① 上游查询已按 tenant 过滤、本条只是按已校验的主键取回细节；
#: ② 按主键取数且函数体内另有 owner/staff 归属校验（越权 403/404）。
#: 逐条理由见各条目；不符合这两类的请补条件，不要往这里加。
EXEMPT: dict[str, str] = {
    # --- chat：会话写路径（_check_session_access 是统一闸门）---
    "backend/app/api/chat.py::_check_session_access::Session": "闸门本身：按 PK 取会话后立刻做 owner-or-staff 判定，非属主 404 防探测",
    "backend/app/api/chat.py::_update_conv_state_locked::Session": "with_for_update 按 PK 重读同一会话行；调用方已过 _check_session_access",
    "backend/app/api/chat.py::_mark_clarifying_locked::Session": "同上（M7 行锁重读），入口同一闸门",
    "backend/app/api/chat.py::_fetch_history._work::Message": "按已校验 session_id 取历史消息，租户随会话确定",
    # --- feedback：消息→会话→属主三段归属校验 ---
    "backend/app/api/feedback.py::submit_feedback::Message": "按 PK 取消息，紧随其后用其 session_id 校验属主（不符 403）",
    "backend/app/api/feedback.py::submit_feedback::Session": "上一步的归属校验本体（s.user_id != user_id → 403）",
    "backend/app/api/feedback.py::submit_feedback::Feedback": "按 message_id + 当前 user_id 取自己的反馈行（天然不跨用户）",
    # --- sessions：详情/删除/评分/代发均为「先校验会话归属，再按 PK 取细节」---
    "backend/app/api/sessions.py::get_session::Session": "按 PK 取会话后 owner-or-staff 判定，非属主 403",
    "backend/app/api/sessions.py::get_session::Message": "已过归属闸门的会话，按 session_id 取消息",
    "backend/app/api/sessions.py::get_session::MessageSource": "按上一步已过滤消息的 id 集合取引用来源",
    "backend/app/api/sessions.py::post_agent_message::Session": "require_roles(admin,agent) 可写任意客户会话（客服工作台语义）",
    "backend/app/api/sessions.py::post_agent_message::User": "按 JWT sub 取操作人本人行（签名 token 来源，非外部输入）",
    "backend/app/api/sessions.py::delete_session::Session": "按 PK 取会话后 owner-or-admin 判定，非属主 404 防探测",
    "backend/app/api/sessions.py::delete_session::Ticket": "删除前置检查：按刚校验过的 session_id 查其活跃工单",
    "backend/app/api/sessions.py::rate_satisfaction::Session": "按 PK 取会话，同行内 s.user_id != user_id → 404 防探测",
    "backend/app/api/sessions.py::_latest_user_message::Message": "suggest 私有 helper，端点侧 require_roles(admin,agent) 已守；按 session_id 取",
    "backend/app/api/sessions.py::_load_session_conv_state::Session": "按 PK 取会话（suggest staff 路径），不存在 404",
    "backend/app/api/sessions.py::list_sessions::User": "user_ids 取自本函数已按 tenant 过滤的会话行，仅补 BUG-12 客户标识",
    # --- tickets：staff 管理面 / 上游 Ticket 已带 tenant ---
    "backend/app/api/tickets.py::create_ticket::Session": "require_roles(admin,agent) 代客建单；仅校验会话存在（C7 已登记为有意）",
    "backend/app/api/tickets.py::create_ticket::Ticket": "幂等检查：按刚定位的 session_id 查活跃工单",
    "backend/app/api/tickets.py::escalate_ticket::Session": "按 PK 取会话，紧随其后 s.user_id != payload[sub] → 403",
    "backend/app/api/tickets.py::list_tickets::Session": "session_ids 来自本函数已带 Ticket.tenant_id 的列表行，仅补会话主题",
    "backend/app/api/tickets.py::list_my_tickets::Session": "join 的 Ticket 侧已带 tenant_id + Session.user_id==本人，仅补会话主题",
    # --- services：由已校验调用方进入 / kb_id 来自租户过滤后的 KB 定位 ---
    "backend/app/services/agent_assist.py::fetch_history::Message": "suggest 复用 helper，端点侧 staff 守卫已守；按 session_id 取",
    "backend/app/services/kb_lookup.py::kb_version_str::Document": "kb_id 来自 get_latest_kb_id（已按 TENANT_DEFAULT 过滤）的版本指纹聚合",
    "backend/app/services/knowledge_import_service.py::_check_quick_coverage::Document": "导入任务内按本任务 kb_id 校验覆盖率，kb_id 由导入入口按租户解析",
    "backend/app/services/ticket_service.py::ensure_active_ticket::Ticket": "AI 建单 helper：session_id 来自已过 _check_session_access 的请求",
}


def _tenant_model_names() -> set[str]:
    """模型类名集合：类体里显式写了 `tenant_id = ...`（红线⑨ / tenant_id_column()）。"""
    names: set[str] = set()
    for path in sorted(MODELS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                targets: list[ast.expr] = []
                if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                    targets = [*stmt.targets] if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(isinstance(t, ast.Name) and t.id == "tenant_id" for t in targets):
                    names.add(node.name)
    return names


def _model_in_args(node: ast.Call, models: set[str]) -> set[str]:
    """select(...) 位置参数里出现的租户模型：`Model` 本体或 `Model.column`（含 func.count(Model.id)）。"""
    found: set[str] = set()
    for arg in node.args:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                if sub.value.id in models:
                    found.add(sub.value.id)
            elif isinstance(sub, ast.Name) and sub.id in models:
                found.add(sub.id)
    return found


def _scan_file(path: Path, models: set[str]) -> list[tuple[str, int, str, str]]:
    """返回该文件的违规项：(豁免键, 行号, 模型名, 语句首行源码)。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))

    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def enclosing_stmt(node: ast.AST) -> ast.AST:
        cur = node
        while (cur := parent.get(cur)) is not None and not isinstance(cur, ast.stmt):
            pass
        return cur if cur is not None else node

    def func_chain(node: ast.AST) -> list[ast.AST | None]:
        """自身所在的全部函数作用域（含嵌套）+ 模块作用域，由内向外。"""
        chain: list[ast.AST | None] = []
        cur: ast.AST | None = node
        while (cur := parent.get(cur)) is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain.append(cur)
        chain.append(None)  # 模块作用域
        return chain

    def qualname(node: ast.AST) -> str:
        parts: list[str] = []
        cur: ast.AST | None = node
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                parts.append(cur.name)
            cur = parent.get(cur)
            if isinstance(cur, ast.Module):
                break
        return ".".join(reversed(parts) or ["<module>"])

    # 条件列表溯源用：同文件顶层函数源码 + 「作用域 → 该作用域内的赋值语句」索引
    toplevel_funcs = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def assign_nodes(scope: ast.AST | None) -> list[ast.stmt]:
        body = scope.body if scope is not None else tree.body
        return [
            s for s in body if isinstance(s, (ast.Assign, ast.AnnAssign))
        ]

    def cond_provenance(stmt: ast.AST) -> bool:
        """语句用到的局部名，其赋值（本作用域内）或所调用的同文件辅助函数里含 tenant_id 即为真。"""
        names = {n.id for n in ast.walk(stmt) if isinstance(n, ast.Name)}
        if not names:
            return False
        scopes: list[ast.AST | None] = func_chain(stmt)  # 由内向外的函数作用域 + 模块作用域
        for scope in scopes:
            for a in assign_nodes(scope):
                targets = a.targets if isinstance(a, ast.Assign) else [a.target]
                if not any(isinstance(t, ast.Name) and t.id in names for t in targets):
                    continue
                frag = ast.get_source_segment(src, a) or ""
                if "tenant_id" in frag:
                    return True
                value = a.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    helper = toplevel_funcs.get(value.func.id)
                    if helper is not None and "tenant_id" in (ast.get_source_segment(src, helper) or ""):
                        return True
        return False

    rel = path.relative_to(ROOT).as_posix()
    bad: list[tuple[str, int, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "select":
            continue
        hit_models = _model_in_args(node, models)
        if not hit_models:
            continue
        stmt = enclosing_stmt(node)
        stmt_src = ast.get_source_segment(src, stmt) or ""
        if "tenant_id" in stmt_src or cond_provenance(stmt):
            continue
        key_fn = qualname(node)
        for model in sorted(hit_models):
            key = f"{rel}::{key_fn}::{model}"
            if key in EXEMPT:
                EXEMPT_USED.add(key)
                continue
            first_line = next((ln.strip() for ln in stmt_src.splitlines() if ln.strip()), "")
            bad.append((key, node.lineno, model, first_line[:100]))
    return bad


EXEMPT_USED: set[str] = set()


def main() -> int:
    if not MODELS_DIR.is_dir():
        print(f"[FAIL] 模型目录缺失：{MODELS_DIR}", file=sys.stderr)
        return 1
    models = _tenant_model_names()
    if not models:
        print("[FAIL] 未识别到任何带 tenant_id 的模型 —— 尺子失效（fail-closed）", file=sys.stderr)
        return 1

    violations: list[tuple[str, int, str, str]] = []
    scanned = 0
    for d in SCAN_DIRS:
        for py in sorted(d.glob("*.py")):
            scanned += 1
            violations += _scan_file(py, models)

    stale = sorted(set(EXEMPT) - EXEMPT_USED)
    print(f"租户模型 {len(models)} 个：{' '.join(sorted(models))}")
    print(f"扫描 {scanned} 个文件，豁免 {len(EXEMPT_USED)} 处，违规 {len(violations)} 处")

    rc = 0
    if violations:
        print(f"\n[FAIL] {len(violations)} 条 select() 未显式按 tenant_id 过滤（红线⑨）：", file=sys.stderr)
        for key, lineno, _model, snippet in violations:
            print(f"  - {key}:{lineno}\n      {snippet}", file=sys.stderr)
        print(
            "\n修法：查询补 `<模型>.tenant_id == settings.TENANT_DEFAULT`（写法照 app/api/tickets.py），"
            "\n      仅当属于「上游已过滤/归属校验后按主键取数」才登记进 scripts/check_tenant_filters.py "
            "EXEMPT 并写明理由。",
            file=sys.stderr,
        )
        rc = 1
    if stale:
        print(f"\n[FAIL] {len(stale)} 条豁免已失效（函数改名/删除/已带过滤，请删掉条目）：", file=sys.stderr)
        for key in stale:
            print(f"  - {key}", file=sys.stderr)
        rc = 1
    if rc == 0:
        print("RESULT: PASS（红线⑨ 读路径全部带租户条件或已登记豁免）")
    return rc


if __name__ == "__main__":
    sys.exit(main())
