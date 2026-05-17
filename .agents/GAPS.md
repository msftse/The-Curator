# Open Gaps — pick up here tomorrow

Snapshot of in-flight + known-but-deferred work as of end-of-session.
See session transcript for full context. AGENTS.md / docs/PRD.md are still the authoritative specs.

---

## 1. Never-delete AST gate is not scope-aware  ✅ DONE

Resolved in commit `900581a`. The gate now walks AST with parent tracking and
allows `delete_blob` only inside `move_published_to_archive` in
`backend/services/curator.py`. All 20 invariant tests pass; full unit suite at
182/182.

---

## 1a. Curator dashboard "Failed to load recent runs"  ✅ DONE

**Root cause:** Backend principal lacks `Storage Blob Data Reader` on
`stskillhubdeveastus2`, so `list_blobs(name_starts_with="runs/")` returns
`AuthorizationFailure`. The endpoint propagated the raw `HttpResponseError`
as an unhandled 500, which strips CORS headers — browser surfaces it as
`TypeError: Failed to fetch`.
**Fix:** Wrap the listing iteration in try/except and return `[]` on
failure (matches the status endpoint's existing pattern for `last_run`).
The UI now correctly renders "No prior runs." Logs the underlying error
as `curator.list_runs.list_failed` for ops to chase.
**Still TODO operationally (not code):** Grant the backend's principal
`Storage Blob Data Reader` (or `Contributor`) on the storage account so
runs actually surface once the curator starts writing them. Without the
role, runs are *invisible* in the UI even though the curator can write
them via its own UAMI in prod.

---

## 2. Token accounting from MAF returns zero  ✅ DONE

**Root cause:** Two unrelated MAF API mismatches in
`backend/services/llm/foundry.py`:

1. `agent_framework._types.UsageDetails` is a `TypedDict` (runtime `dict`),
   so `getattr(usage, "input_token_count", 0)` always returned the default.
   Switched to `usage.get("input_token_count") or 0` (same for output).
2. `ChatResponse` exposes `.model`, not `.model_id`. The previous code
   silently fell back to `self._settings.foundry_deployment` because the
   attribute was missing.

Both fixes are covered by `backend/tests/unit/test_foundry_llm_provider.py`
(5 tests, all passing), including a regression test pinning the TypedDict
`.get(...)` access path.

---

## 3. Curator review path still uses the legacy Inference SDK  ✅ DONE

Migrated in this session. `backend/services/curator_review.py` now:

- Defines `_DriftReview(BaseModel)` and `_ConsolidationReview(BaseModel)` matching the prompts in `curator_review_prompts.py` (both `extra="forbid"`).
- Passes the class as `response_format=_DriftReview` / `response_format=_ConsolidationReview` to MAF for server-side structured-output validation.
- Parses results with `model_validate_json` first, falling back to the existing lenient `_parse_json_object` path so `FakeLLMProvider` (which ignores `response_format`) keeps working in unit tests.
- The "unknown verdict" branch on the drift pass is now unreachable — `_DriftReview.verdict` is `Literal["keep", "patch"]`.

Full suite still 182/182.

---

## 4. Two Foundry endpoint env vars  ✅ DONE

After gap #3, the curator no longer needs the `/models` Inference endpoint.
Dropped from `backend/core/config.py`, `charts/agentic-skill-hub/values.yaml`,
`.env.local`, and `.env.local.example`:

- `FOUNDRY_ENDPOINT` (no longer read anywhere)
- `FOUNDRY_API_VERSION` (no longer read anywhere)

Single Foundry endpoint env var remains: `AZURE_AI_PROJECT_ENDPOINT`. Both
classifier and curator route through it via MAF's `FoundryChatClient`.

---

## 5. `azure-ai-inference` is still in `pyproject.toml`  ✅ PARTIAL

Removed as a **direct** dependency from `pyproject.toml`. It survives in
`uv.lock` as a transitive of `agent-framework-foundry` (via
`agent-framework-openai`). Nothing actionable on our side until MAF drops
that transitive — `uv tree` confirms there's no other ancestor.

---

## 6. Classifier worker is not managed by `make`  ✅ DONE

Added `make dev` target that brings up `uvicorn` + the classifier worker
together. Worker is backgrounded to `/tmp/skillhub-worker.log`; uvicorn
runs in the foreground. A shell trap kills the worker on Ctrl-C so we
don't leak stale workers between sessions (the original symptom — cached
StubClassifier across code edits — should no longer recur).

`make worker` still exists for the rare "worker only" loop. README's
local-dev section unchanged: `make dev` is the recommended entrypoint.

---

## 7. Uncommitted branch is large

**State:** 33+ modified files spanning classifier MAF rewrite, category/tags upload feature, detail-page redesign, and AGENTS.md edits. Nothing committed this session.
**Suggested split:**
1. **Classifier → MAF + structured outputs** (`backend/services/llm/foundry.py`, `provider.py`, `classifier_stub.py`, `pyproject.toml`, `.env.local.example`, `backend/core/config.py`).
2. **Category + tags upload feature** (`backend/models/skill.py`, `backend/api/uploads.py`, `backend/services/upload.py`, `backend/workers/classifier.py`, frontend `upload/page.tsx` + `lib/api/client.ts`).
3. **Detail page redesign** (frontend `components/catalog/SkillDetailHeader.tsx`, `SkillDetailMeta.tsx`, `MarkdownView.tsx`).
4. **AGENTS.md never-delete clarification** + the test fix from gap #1 (do these together so the test stays green at every commit).
5. **Makefile + ops polish** (port-8000 guard, worker target from gap #6).
**Hold:** `.env.local` (gitignored — confirmed), `next-env.d.ts` (regenerates).

---

## 8. Stretch / next-after-the-above

- **Curator UI**: PRD §11 + plan `m2.2-frontend-curator-ui.md` is the next functional milestone if no fires.
- **AKS deployment** plan `m4-aks-deployment.md` is staged but not started; depends on M3 being stable.
- **Pre-commit hooks**: ✅ DONE. `.pre-commit-config.yaml` now runs trailing-whitespace / EOF / yaml / large-files / private-key / merge-conflict checks, plus `ruff` (lint) + `ruff format`, plus a local `never-delete-invariant` hook that runs the AST gate test on any `backend/**.py` change. `ruff-pre-commit` pinned to `v0.15.13` to match the project's installed ruff (the older `v0.5.7` produced spurious reformats on first run). Helm template YAML is excluded from `check-yaml` because `{{ … }}` placeholders aren't valid YAML.
- **Token observability**: blocked on gap #2 resolution.

---

## Quick re-orientation tomorrow

1. `cat .agents/GAPS.md` (this file).
2. `uv run pytest backend/tests/unit/ -x` → confirm 180/181, single failure should still be the never-delete AST test.
3. Tail the live classifier worker if still running: `tail -f /tmp/classifier.log`. Otherwise `nohup uv run python -m backend.workers.classifier > /tmp/classifier.log 2>&1 &`.
4. `git status` → review the 33+ uncommitted files against the suggested split in gap #7.

Start with gap #6 (`make worker`) — quality-of-life fix that prevents a
recurrence of the "stale worker" confusion that bit us this session. Then
gap #2 (MAF token accounting) if it's still emitting zeros after MAF beta
ships a fix. Gap #8 is stretch.
