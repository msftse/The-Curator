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

## 2. Token accounting from MAF returns zero

**File:** `backend/services/llm/foundry.py` (around the `get_response` call).
**Symptom:** `usage_details.input_token_count` and `output_token_count` both come back as `0` from `agent-framework-foundry==1.4.0`. Logs show `foundry.llm.response tokens_in=0 tokens_out=0` on every call even when text is correctly returned.
**Impact:** Non-blocking — classifier works end-to-end. But it kills any future "cost per classify" telemetry and any quota dashboards.
**Next step:** Open an issue against the `agent-framework` repo or check if the beta exposes usage under a different attribute (e.g. `response.raw.usage`). If MAF can't surface it, fall back to manually counting tokens with `tiktoken` against the rendered prompt + response.

---

## 3. Curator review path still uses the legacy Inference SDK

**File:** `backend/services/curator_review.py` + the same `FoundryLLMProvider` it shares with the classifier.
**Current state:** Curator calls `provider.complete(response_format="json_object", ...)`. Now that the provider is MAF-backed, `"json_object"` is silently ignored (the prompt does the work) and the curator's lenient JSON parser handles the result. So **it works**, but it doesn't benefit from Pydantic-validated structured outputs the way the classifier does.
**Why this matters:** Curator JSON parse failures are currently best-effort; with a `response_format=PydanticClass` we'd get server-side schema enforcement and the same observability story as the classifier.
**Fix shape:**
- Define `_CuratorReview(BaseModel)` (or equivalent) in `backend/services/curator_review.py` matching the existing JSON contract.
- Swap `response_format="json_object"` → `response_format=_CuratorReview`.
- Drop the lenient parser branch once parity is proven against the existing curator-review unit tests.
**Risk:** Curator dry-run + rollback round-trip tests must still pass. AGENTS.md §5 invariants are unchanged by this work.

---

## 4. Two Foundry endpoint env vars is a foot-gun

**Files:** `.env.local.example`, `.env.local`, `backend/core/config.py`.
**State:** We require both `FOUNDRY_ENDPOINT` (`/models` shape, legacy Inference SDK, used by curator) **and** `AZURE_AI_PROJECT_ENDPOINT` (`/api/projects/<name>` shape, MAF, used by classifier). Documented in the .example file, but a future contributor will absolutely set one and forget the other.
**Resolution options:**
1. Wait until gap #3 is fixed (curator migrated to MAF), then **delete `FOUNDRY_ENDPOINT` + `FOUNDRY_API_VERSION` entirely** and drop the `azure-ai-inference` dep.
2. Or: derive one from the other in `Settings` (the project endpoint is `<base>/api/projects/<name>`, the inference endpoint is `<base>/models` — both share the `<base>` hostname).
**Recommended:** Do #1 right after #3.

---

## 5. `azure-ai-inference` is still in `pyproject.toml`

Stays installed only because curator still imports it. Removable after gap #3 lands. `agent-framework-foundry>=1.0.0b9` + `azure-ai-agents>=1.2.0b5` are the keepers.

---

## 6. Classifier worker is not managed by `make`

**Symptom:** This session we ran the worker as `nohup uv run python -m backend.workers.classifier > /tmp/classifier.log 2>&1 &`. There's no `make worker` / `make dev` target that brings up the worker alongside uvicorn, and `uvicorn --reload` does not restart it on code changes.
**Fix shape:** Add a `make worker` target and/or wire `honcho` / `foreman` (Procfile) to bring `api` + `worker` + (optional) `curator_scheduler` up together with one command. Document in README's local-dev section.
**Why it bit us:** Code changes to `backend/services/classifier_stub.py` looked like they weren't taking effect — the worker had cached the old StubClassifier because we never restarted it.

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
- **Pre-commit hooks**: AGENTS.md §10 calls for `.pre-commit-config.yaml`; not present yet. Ruff + the never-delete gate would be the obvious first hooks.
- **Token observability**: blocked on gap #2 resolution.

---

## Quick re-orientation tomorrow

1. `cat .agents/GAPS.md` (this file).
2. `uv run pytest backend/tests/unit/ -x` → confirm 180/181, single failure should still be the never-delete AST test.
3. Tail the live classifier worker if still running: `tail -f /tmp/classifier.log`. Otherwise `nohup uv run python -m backend.workers.classifier > /tmp/classifier.log 2>&1 &`.
4. `git status` → review the 33+ uncommitted files against the suggested split in gap #7.

Start with gap #3 (curator MAF migration) — it unlocks gaps #4 and #5 for free.
Then gap #6 (`make worker`) is a quality-of-life fix that prevents a recurrence
of the "stale worker" confusion that bit us this session.
