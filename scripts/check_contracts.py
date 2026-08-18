#!/usr/bin/env python3
"""契约校验：根契约 contracts/api.ts vs 后端 OpenAPI contracts/api-schema.json。

契约分类（见 contracts/README.md）：
  A. 接口类型（HTTP req/resp）—— 与 OpenAPI components.schemas 同名，做字段级比对（FAIL 项）；
  B. SSE 事件类型（/chat/stream 协议：SSEStage/SSEEvent/...）—— OpenAPI 无对应，仅列出不比对；
  C. 前端私有状态 —— 仅前端使用，仅列出不比对。

差异基线：首次运行用 `--init-baseline` 把当前差异固化为 contracts/.check-baseline.json；
之后运行只报告【新增】差异（基线外的漂移）为 FAIL，避免历史命名差异造成永久噪音。

用法：
  python scripts/check_contracts.py [--init-baseline] [--contract ...] [--schema ...]
退出码：0 = 通过；1 = 存在新增差异。
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'contracts' / '.check-baseline.json'

# FastAPI 自动生成的模型，不参与契约比对
AUTO_MODELS = {'HTTPValidationError', 'ValidationError'}
# OpenAPI schema 名（非自动生成、契约无对应类型）按此列表忽略：
# - 契约已有对应（命名差异）：AdminSettingsResp↔AdminSettings、KnowledgeSearchHit↔KnowledgeHit、
#   FaqDocItem↔FaqDoc、FaqKbItem↔FaqKBItem、FaqListResp↔PublicFaqResp、UserRole↔Role
# - 后端模型未回填契约（KNOWN_GAP，见 contracts/README.md，待后续轮次回填）：CreateSessionReq、
#   CreateTicketReq、SatisfactionReq、StatusUpdateReq、AgentReplyReq、FrontendErrorReq、SessionItem、
#   SessionMessage、FeedbackItem、FeedbackListResp、FeedbackResp、ModelSettings、QuotaSettings、
#   RagSettings、RateLimitSettings、FeedbackRating
IGNORE_EXTRA = {
    'ModelSettings', 'QuotaSettings', 'RagSettings', 'RateLimitSettings',
    'UserRole', 'FeedbackRating', 'Body_upload_document_api_v1_knowledge_bases__kb_id__documents_post',
    'AdminSettingsResp', 'KnowledgeSearchHit', 'FaqDocItem', 'FaqKbItem', 'FaqListResp',
    'CreateSessionReq', 'CreateTicketReq', 'SatisfactionReq', 'StatusUpdateReq', 'AgentReplyReq',
    'FrontendErrorReq', 'SessionItem', 'SessionMessage', 'FeedbackItem', 'FeedbackListResp', 'FeedbackResp',
}

INTERFACE_RE = re.compile(r'^export\s+interface\s+(\w+)\s*\{', re.M)
TYPE_RE = re.compile(r'^export\s+type\s+(\w+)\s*=', re.M)
FIELD_RE = re.compile(r'^\s*(\w+)(\??):', re.M)


def strip_comments(ts_text: str) -> str:
    text = re.sub(r'/\*.*?\*/', '', ts_text, flags=re.S)
    return re.sub(r'//.*?$', '', text, flags=re.M)


def parse_contract(ts_path: str) -> dict:
    """返回 {类型名: {字段名: 是否可选}}。

    用行扫描：每个顶层声明从行首 `export interface|type|const` 开始，到下一个顶层声明行结束。
    不依赖大括号配对，避免嵌套/字符串中的 `{` `}` 破坏配对。
    """
    text = strip_comments(Path(ts_path).read_text(encoding='utf-8'))
    lines = text.split('\n')
    types: dict = {}
    cur: str | None = None  # 当前 interface 名
    body: list[str] = []
    TOP_RE = re.compile(r'^export\s+(interface|type|const)\s+(\w+)')

    for line in lines:
        tm = TOP_RE.match(line)
        if tm:
            if cur is not None:
                types[cur] = collect_fields(body)
            kind, name = tm.group(1), tm.group(2)
            if kind == 'interface':
                cur = name
                body = []
            else:
                cur = None  # type/const 别名暂不展开，只登记名字
                types.setdefault(name, {})
        elif cur is not None:
            body.append(line)
    if cur is not None:
        types[cur] = collect_fields(body)
    return types


def collect_fields(body: list[str]) -> dict:
    """从 interface body 行提取顶层字段名与可选标记。嵌套对象字段（缩进 > 顶层）可能被捕获，属已知近似。"""
    fields: dict = {}
    for line in body:
        m = FIELD_RE.match(line)
        if m:
            fields[m.group(1)] = m.group(2) == '?'
    return fields


def parse_openapi(json_path: str) -> dict:
    data = json.loads(Path(json_path).read_text(encoding='utf-8'))
    return data.get('components', {}).get('schemas', {}) or {}


def field_optional(schema: dict) -> dict:
    props = schema.get('properties', {}) if isinstance(schema, dict) else {}
    required = set(schema.get('required', [])) if isinstance(schema, dict) else set()
    return {k: (k not in required) for k in props}


def fmt_fields(fl: list, limit: int = 8) -> str:
    s = sorted(fl)
    shown = ', '.join(s[:limit])
    return shown + (f", ...({len(s)})" if len(s) > limit else '')


def collect_diff(contract: dict, openapi: dict) -> dict:
    a_miss, a_extra, opt_diff, b_types, c_types, backend_extra = [], [], [], [], [], []

    for name, fields in contract.items():
        if name in openapi:
            ofields = field_optional(openapi[name])
            miss = {k for k in fields if k not in ofields}
            extra = {k for k in ofields if k not in fields}
            for k in fields.keys() & ofields.keys():
                if fields[k] != ofields[k]:
                    opt_diff.append(f'{name}.{k}(契约{"可选" if fields[k] else "必填"}/schema{"可选" if ofields[k] else "必填"})')
            if miss:
                a_miss.append(f'{name}: 契约字段不在 openapi [{fmt_fields(miss)}]')
            if extra:
                a_extra.append(f'{name}: openapi 字段不在契约 [{fmt_fields(extra)}]')
        elif 'SSE' in name or 'Event' in name or 'Stage' in name:
            b_types.append(name)
        else:
            c_types.append(name)

    for name in openapi:
        if name in AUTO_MODELS or name in IGNORE_EXTRA:
            continue
        if name not in contract:
            backend_extra.append(name)

    return {
        'a_miss': sorted(a_miss),
        'a_extra': sorted(a_extra),
        'opt_diff': sorted(opt_diff),
        'b_types': sorted(b_types),
        'c_types': sorted(c_types),
        'backend_extra': sorted(backend_extra),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--init-baseline', action='store_true', help='把当前差异固化为基线')
    ap.add_argument('--contract', default=str(ROOT / 'contracts' / 'api.ts'))
    ap.add_argument('--schema', default=str(ROOT / 'contracts' / 'api-schema.json'))
    args = ap.parse_args()

    contract = parse_contract(args.contract)
    openapi = parse_openapi(args.schema)
    d = collect_diff(contract, openapi)

    print('=== 契约校验 ===')
    print(f'契约类型数: {len(contract)} | OpenAPI schema 数: {len(openapi)}')
    print(f'B 类(SSE 事件, 不比对): {d["b_types"]}')
    print(f'C 类(前端私有, 不比对): {len(d["c_types"])} 个: {d["c_types"][:15]}')

    def dump(key, title):
        items = d[key]
        if not items:
            print(f'[OK] {title}: 0')
        else:
            print(f'[{'WARN' if key == "opt_diff" else 'DIFF'}] {title}: {len(items)}')
            for it in items[:15]:
                print(f'    - {it}')
            if len(items) > 15:
                print(f'    ... 共 {len(items)} 项，见 baseline')

    dump('a_miss', 'A 类字段缺失(契约有/openapi无)')
    dump('a_extra', 'A 类字段多余(openapi有/契约无)')
    dump('opt_diff', '可选性不一致(仅提示)')
    dump('backend_extra', 'OpenAPI 有但契约无(未在 IGNORE_EXTRA)')

    baseline = {k: d[k] for k in ('a_miss', 'a_extra', 'backend_extra')}
    if args.init_baseline:
        BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'RESULT: BASELINE SAVED -> {BASELINE.name}')
        return 0

    if BASELINE.exists():
        prev = json.loads(BASELINE.read_text(encoding='utf-8'))
        new_issues = []
        for k in ('a_miss', 'a_extra', 'backend_extra'):
            for item in baseline[k]:
                if item not in prev.get(k, []):
                    new_issues.append(f'{k}: {item}')
        if new_issues:
            print('--- 新增漂移(FAIL) ---')
            for it in new_issues[:30]:
                print(' *', it)
            print(f'RESULT: FAIL ({len(new_issues)} 新增)')
            return 1
        print('RESULT: PASS (无新增漂移)')
        return 0

    if d['a_miss'] or d['a_extra'] or d['backend_extra']:
        print('RESULT: FAIL (首次运行无基线，请先 --init-baseline 审视当前差异并固化)')
        return 1
    print('RESULT: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
