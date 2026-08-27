# Progress Log

## 2026-08-23

- Inspected uncommitted work and confirmed the user-approved scope.
- Established CI/test seams: frontend Axios adapter test, backend FastAPI endpoint tests, and script-level evaluation commands.
- Next: add the missing regression coverage before completing implementation.
- Plan review corrected the direct dependency test: `require_roles()` returns a synchronous FastAPI dependency. The cache-key event will reuse the existing domain name `rewritten_query`.
- Red test evidence: the RAG `done` event lacks `rewritten_query`; Chat then calls `rewrite()` again. Focused tests run with `--no-cov` because the repository-wide 70% coverage gate is not meaningful for a two-test TDD slice.
- Corrected one implementation-placement error before green verification: the non-cache QA done event now carries `rewritten_query`; the Chat test tolerates the intentionally removed import.
- Backend focused green verification passed in an ephemeral container: 2 tests passed. Next, temporarily restore the pre-fix refresh predicate to prove the new frontend regression test fails before returning the exact comparison.
- Frontend red verification passed: with the legacy `includes` predicate, `/auth/refresh-status` rejected with 401 exactly as the new test expects. The exact predicate has been restored for green verification.
- Frontend green verification passed: 4/4 client interceptor tests, with the prior jsdom-navigation test noise removed.
- Added a direct staff-role dependency test; an intentional inverted-membership mutation was observed to fail for admin access before restoring the correct guard.
- Full backend lint initially reported only `test_deps.py` import ordering. The test import order has been corrected; lint will be rerun before full pytest.
