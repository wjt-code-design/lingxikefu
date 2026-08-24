# CI and Quality Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make frontend regressions visible in CI, remove three small correctness/performance risks, and evidence-check the RAG baseline without widening the deferred scope.

**Architecture:** Keep responsibility at existing seams: GitHub Actions invokes package scripts, Axios tests exercise the configured HTTP client through its adapter, FastAPI endpoints continue to own authorization policy via a dependency, and the chat stream carries the already-produced cache key to its persistence branch. No new service or data model is introduced.

**Tech Stack:** GitHub Actions, Node 22/npm, TypeScript/Vitest, FastAPI/Python, pytest, PostgreSQL/Qdrant-backed evaluation scripts.

## Global Constraints

- Preserve unrelated uncommitted changes.
- Run frontend checks through `package.json` scripts, not duplicated CLI flags.
- Do not implement deferred M2/M4/L1/D/F items.
- Evaluation results must come from a live configured stack; missing infrastructure is recorded, never fabricated.

---

### Task 1: Protect the exact refresh endpoint exclusion

**Files:**
- Modify: `frontend/src/tests/client.test.ts`
- Verify: `frontend/src/api/client.ts:66-98`

**Interfaces:**
- Consumes: `http` Axios instance and its adapter seam.
- Produces: a regression test that distinguishes `/auth/refresh` from a similarly named protected route.

- [ ] **Step 1: Write the failing test**

```ts
it('仅排除 refresh 本身；/auth/refresh-status 的 401 仍会刷新', async () => {
  let calls = 0;
  mockAdapter((config) => {
    if (config.url === '/auth/refresh') return { status: 200, data: { access_token: 'new' } };
    if (config.url === '/auth/refresh-status' && ++calls === 1) return { status: 401, data: {} };
    return { status: 200, data: { ok: true } };
  });
  await expect(http.get('/auth/refresh-status')).resolves.toMatchObject({ data: { ok: true } });
  expect(useAuthStore.getState().token).toBe('new');
});
```

- [ ] **Step 2: Run test to verify it fails with the old `includes` predicate**

Run: `npm test -- client.test.ts`

Expected: the request rejects with 401 because `/auth/refresh-status` was mistakenly excluded.

- [ ] **Step 3: Keep the minimal exact URL predicate**

```ts
original.url !== '/auth/refresh'
```

- [ ] **Step 4: Run the focused test**

Run: `npm test -- client.test.ts`

Expected: all client interceptor tests pass.

### Task 2: Validate and complete the staff-role dependency extraction

**Files:**
- Modify: `backend/tests/test_sessions_messages.py`
- Verify: `backend/app/api/deps.py`, `backend/app/api/customers.py`, `backend/app/api/sessions.py`, `backend/app/api/tickets.py`

**Interfaces:**
- Consumes: `require_roles(*roles)` as a FastAPI dependency.
- Produces: unchanged 403 behavior for a `user` accessing a staff-only endpoint and successful agent authorization.

- [ ] **Step 1: Run the existing public authorization tests before changing implementation**

Run: `pytest tests/test_sessions_messages.py tests/test_tickets.py -q`

Expected: staff requests succeed and user requests are denied with 403.

- [ ] **Step 2: Add a direct dependency unit test if endpoint coverage does not prove both allowed roles**

```python
def test_require_roles_accepts_configured_roles_and_rejects_user():
    checker = require_roles("admin", "agent")
    assert checker({"role": "admin"})["role"] == "admin"
    assert checker({"role": "agent"})["role"] == "agent"
    with pytest.raises(HTTPException) as exc:
        checker({"role": "user"})
    assert exc.value.status_code == 403
```

- [ ] **Step 3: Run focused backend tests**

Run: `pytest tests/test_sessions_messages.py tests/test_tickets.py -q`

Expected: pass with no role-policy behavior change.

### Task 3: Reuse the pipeline rewrite result for cache insertion

**Files:**
- Modify: `backend/app/services/rag_service.py` and `backend/app/api/chat.py`
- Test: existing chat stream tests or a focused new test only if no public stream seam exposes cache events.

**Interfaces:**
- Consumes: `stream_answer(... )` event tuples.
- Produces: an event payload containing `RagResult.rewritten_query` so `chat.py` calls `cache_put()` without invoking `rewrite()` a second time.

- [ ] **Step 1: Locate the `rewrite()` call in `stream_answer` and add an independent failing test at the stream event seam**

```python
events = [event async for event in stream_answer("碎屏显咋换", kb_id, history=[])]
assert next(data for event, data in events if event == "done")["rewritten_query"] == "碎屏险怎么换"
```

- [ ] **Step 2: Run the focused test to observe the missing cache query field**

Run: `pytest tests/test_chat_api.py -q`

Expected: the new assertion fails before implementation.

- [ ] **Step 3: Attach the already-computed rewritten query to the `done` event, then consume it once**

```python
# rag_service.py
yield "done", {"rewritten_query": result.rewritten_query, ...}

# chat.py
rewritten_query = data.get("rewritten_query")
if not cache_hit and intent == "qa" and content and rewritten_query:
    cache_put(..., rewritten_query, ...)
```

- [ ] **Step 4: Run the chat stream test and confirm only one rewrite happens per cacheable request**

Run: `pytest tests/test_chat_api.py -q`

Expected: pass; a cacheable response uses the event key and a spy confirms that `chat.py` does not call `rewrite()`.

### Task 4: Make frontend CI use the package contract

**Files:**
- Modify: `.github/workflows/ci.yml`
- Verify: `frontend/package.json`

**Interfaces:**
- Consumes: `npm run typecheck` and `npm test` scripts.
- Produces: a Node 22 GitHub Actions job that installs a lockfile-exact dependency tree before type checking and unit testing.

- [ ] **Step 1: Change the workflow commands**

```yaml
- name: Typecheck
  run: npm run typecheck
- name: Unit tests
  run: npm test
```

- [ ] **Step 2: Validate the same commands locally**

Run: `npm run typecheck; npm test`

Expected: exit code 0.

### Task 5: Verify and run the frozen baseline

**Files:**
- Modify: `task_plan.md`, `findings.md`, `progress.md`
- Verify: `backend/scripts/eval_faithfulness.py`, `backend/scripts/eval_recall.py`

**Interfaces:**
- Consumes: current local PostgreSQL/Qdrant/LLM configuration and the frozen corpus under `eval-and-samples`.
- Produces: dated reproducible evaluation output or a concrete infrastructure blocker.

- [ ] **Step 1: Run static and test verification**

Run: `ruff check app tests alembic scripts; pytest`

Expected: exit code 0.

- [ ] **Step 2: Run evaluation scripts from `backend`**

Run: `python scripts/eval_recall.py; python scripts/eval_faithfulness.py`

Expected: each prints a threshold result backed by the frozen fixtures, or an explicit unavailable-service/configuration error.

- [ ] **Step 3: Record fresh commands, outputs, and any blocker in the planning files**

```markdown
## Evaluation baseline
- recall command/result: ...
- faithfulness command/result: ...
- infrastructure state: ...
```
