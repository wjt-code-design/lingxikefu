# Findings

- The previous worker already moved the workflow to repository-root `.github/workflows/ci.yml` and added a frontend job, but the work is uncommitted.
- `frontend/src/api/client.ts` has changed from `includes('/auth/refresh')` to exact equality, but its test suite does not yet protect the false-positive regression.
- `require_roles()` exists and has replaced five staff-only handlers. Existing endpoint tests already cover the public 403 behavior for agent messages and tickets.
- `backend/app/api/chat.py` still recomputes `rewrite(req.content, history)` in the `done` handler even though the stream pipeline has already performed the rewrite internally. The returned rewrite value must be surfaced through the event data before deleting the duplicate invocation.
- The evaluation scripts now support direct execution by inserting the backend root on `sys.path`; they need a live PostgreSQL, Qdrant, configured LLM credentials, and seeded KB data to produce a meaningful result.
