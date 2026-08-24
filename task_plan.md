# CI and Quality Hardening Plan

## Goal

Complete the interrupted CI and small reliability hardening work without expanding into the deferred refactors or product decisions.

## Phases

- [x] Phase 1: Inspect the interrupted changes, existing tests, and evaluation entry points.
- [ ] Phase 2: Add regression coverage for exact refresh matching and role dependency behavior.
- [ ] Phase 3: Complete the duplicate-rewrite removal and align CI with project scripts.
- [ ] Phase 4: Run focused and full verification, then run the frozen evaluation baseline where local services permit it.
- [ ] Phase 5: Record outcomes and remaining operational blockers.

## Scope boundaries

- Included: root GitHub Actions frontend job; exact 401 refresh exclusion; reusable staff-role dependency; duplicate `rewrite()` avoidance in the chat cache path; baseline evaluation run.
- Excluded: ChatContainer/FaqPage/CSS refactors, BroadcastChannel, quota Lua changes, tenant isolation sweep, UI product decisions, Docker rebuild, and test-scaffold consolidation.

## Verification contract

- `npm run typecheck` and `npm test` run successfully from `frontend`.
- Backend focused tests cover staff authorization and the chat cache/rewrite behavior where a stable seam exists.
- `ruff check app tests alembic scripts` and `pytest` run from `backend`.
- Both evaluation scripts are run against the existing local service state; unavailable dependencies are recorded as a blocker rather than inferred.

## Errors encountered

| Error | Attempt | Resolution |
|---|---:|---|
| `rg` pattern parse error while locating stream events | 1 | Re-ran with separate `-e` expressions; no source change was made. |
| Local `pytest` unavailable | 1 | Project `.venv` points to missing Python 3.12; run tests in an ephemeral container with the workspace mounted. |
| Focused red test tripped missing fake `refund()` | 1 | Added the method to the fake so the intended cache-write assertion is observable. |
| First green attempt attached `rewritten_query` to the cache-hit done event only | 1 | Added the field to the normal QA completion event, which is the cache-write path. |
