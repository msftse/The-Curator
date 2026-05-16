# Feature: M3 — Curator LLM Review Pass (Azure AI Foundry, Manager-Approved Proposals)

The following plan is the implementation contract for M3 of the Agentic Skill Hub. It layers an **aux-model review pass** on top of the M2 deterministic curator (`.agents/plans/m2-curator.md`, `backend/services/curator.py`) and exposes the results as **manager-approvable proposals** in a new Cosmos container.

**This pass NEVER mutates skills directly.** Like the deterministic curator, it can only suggest. Unlike the deterministic curator, even archive-style suggestions land as proposals — they require a manager click before any byte moves. The same `lock:curator:run` Redis lock prevents the review pass and the deterministic pass from racing each other.

Pay special attention to:

- **AGENTS.md §5 — never-delete invariant.** The LLM may suggest a "merge A+B into C" or a "patch this SKILL.md" — neither code path may call `delete_item` or `delete_blob`. Merge is modelled as "create new skill C (pending) + archive A and B (status flip; bytes copied to `archive/` as in M2)". The existing AST gate at `backend/tests/unit/test_never_delete_invariant.py:26-36` is extended in Task 6 to cover the new review module + approval handler + LLM provider.
- **AGENTS.md §4 rule #1 — Cosmos-first writes.** Proposals are persisted to a new `review_proposals` Cosmos container (PK `/run_id`) *before* the LLM run returns to the caller. Approval handlers (Task 14) apply the proposal in the same Cosmos-first → audit → cache-bust order as `backend/services/publish.py:88-110` and `backend/services/curator.py:307-347`.
- **Foundry-only.** The aux-model provider is `Azure AI Foundry` (Microsoft Foundry) via the `azure-ai-inference` SDK. Per user direction, **do not add OpenAI or Anthropic clients**. A thin `LLMProvider` ABC isolates the call site so tests inject a `FakeLLMProvider`; the production wiring is a single `FoundryLLMProvider`. Adding any other provider class is out-of-scope.
- **Cost guard is mandatory.** Hard per-run skill cap (`auxiliary.curator_review.max_skills_per_run`, default `50`) and hard per-review token cap (`max_input_tokens` + `max_output_tokens`). On breach: abort the run, write a `CuratorReviewRunRecord` with `aborted_reason="cost_cap"`, raise no exception — managers see a status row.
- **Manager approval gate is non-bypassable.** There is NO `--auto-apply` flag. Even an admin cannot bypass review in M3. A future trusted-bot bypass is explicitly out of scope.
- **Lock reuse, not new lock.** Use the existing `key_curator_run_lock()` from `backend/core/redis.py`. A review pass running blocks a deterministic pass and vice versa. This is the desired behaviour (they read the same `skills` rows and both write reports; sequencing them is fine).

## Feature Description

After the deterministic curator pass moves stale skills through `approved → stale → archived` based purely on usage counters, the **LLM review pass** examines the *content* of active agent-created skills and emits three kinds of structured proposals:

1. **Consolidation candidate** — "skills A and B overlap ≥X%; merge into umbrella skill C with this proposed SKILL.md".
2. **Drift patch** — "skill A's SKILL.md drifted from its description / has dead tool references / has a typo cluster; here is a suggested unified-diff patch".
3. **Keep as-is** — explicit verdict, recorded so we can audit "the model looked at this and decided nothing was wrong".

The pass:

- Runs after the deterministic curator pass (separate scheduler tick or admin endpoint `POST /v1/admin/curator/review`).
- Loads up to `max_skills_per_run` skills with `status='approved'` and `pinned=False` (pinned skills are still **excluded** — pinning means "do not touch", which extends to suggestions too; managers can opt-in by un-pinning).
- For each selected skill, downloads the published bundle tar from Blob and extracts the SKILL.md text (the file body, not the Cosmos `skill_md_text` cache, which can drift if a version was uploaded with an updated bundle but the Cosmos `skill_md_text` wasn't refreshed — Blob is the source of truth for bytes).
- Calls Foundry once per skill (drift verdict) and once per candidate pair for consolidation (capped). Token-bounded.
- Writes each verdict + each suggestion as a row in `review_proposals` with `status="pending"`.
- Extends the curator per-run report (`run.json`/`REPORT.md` in `{curator_reports_container}/{run_id}/`) with a `proposals` section.

Managers then review via `GET /v1/admin/curator/reviews` (list) and `POST /v1/admin/curator/reviews/{proposal_id}/{approve,reject}`. Approve dispatches to the corresponding apply handler (patch or merge), which wraps the mutation in the same snapshot + audit + cache-bust machinery used by the deterministic curator.

Two design choices worth flagging upfront:

- **Snapshot is taken at proposal-apply time, not at review-pass time.** The review pass produces no mutations, so the M2 snapshot-before-pass rule does not apply. Each *apply* call (Task 14) takes its own snapshot via the existing `snapshot_svc.snapshot_published(...)` so a one-click rollback can always undo one approval.
- **No bundle rewrite at proposal time.** The model proposes a *patch* (text-level unified diff) or a *new SKILL.md body* (for merges); we do not regenerate the bundle. Applying a patch rebuilds the bundle from the existing files with the SKILL.md substituted, then runs the same `build_tar` / `put_published` pipeline used by `backend/services/publish.py:65-74`.

## User Story

As a **manager** I want the system to surface "these two skills look 80% redundant" or "this skill's SKILL.md mentions a deprecated tool" so I can fix catalog quality issues I would never catch by reading each skill individually — but I want to be the one who clicks "apply" so the platform never silently rewrites my team's work.

As a **platform admin** I want the LLM review pass to have hard token and skill caps so an aux-model bug or prompt regression cannot run up our Foundry bill or take down the deterministic curator window.

As a **contributor** I want any LLM-proposed change to my skill to land in front of a human, not auto-merged, so my authorship is preserved and a regression is recoverable in one click.

As a **future debugger** I want every proposal — accepted, rejected, or expired — to live in `review_proposals` with the exact LLM input + output and the snapshot name the apply step took, so I can replay any approval decision and prove what the model saw at decision time.

## Problem Statement

The deterministic curator (M2) handles *usage decay* — skills nobody loads get archived. It cannot reason about *content quality*. Two real failure modes M2 does not address:

1. **Near-duplicate proliferation.** Three contributors write almost-identical "deploy-to-azure" skills with slightly different names; the deterministic curator sees all three are loaded weekly and keeps all three. The catalog grows noisy, agent runtimes pick semi-randomly, quality degrades.
2. **Silent drift.** A skill's SKILL.md references a tool, command, or env var that the broader org deprecated; usage may still be high (loaders haven't noticed yet); the deterministic curator has no signal.

We need to plug an LLM into the curator loop **without** giving it write access. Failures the design must prevent:

- An LLM hallucination silently rewriting a SKILL.md. **Mitigation:** no auto-apply, ever. Manager-approval gate.
- A prompt regression archiving good skills. **Mitigation:** LLM cannot archive — only `proposals` of `kind="merge"` or `kind="patch"` are accepted, and `merge` itself only flips status when the manager approves.
- A cost runaway. **Mitigation:** hard skill cap, hard token cap, abort-on-breach behaviour with a recorded run record.
- A concurrent deterministic pass and review pass racing each other on the same skill's `_etag`. **Mitigation:** reuse `key_curator_run_lock()`.
- A reviewer reading stale LLM output (model rerun produced different verdict). **Mitigation:** every proposal stores `model_version`, `prompt_version`, `input_hash`; the approval handler refuses to apply a proposal whose snapshot of the skill no longer matches the current Cosmos `_etag` — re-review required.

## Solution Statement

1. **New Cosmos container `review_proposals` (Task 5).** PK `/run_id`. One doc per proposal. Schema in Task 4.
2. **`LLMProvider` ABC + `FoundryLLMProvider` impl + `FakeLLMProvider` for tests (Tasks 7–9).** Foundry impl uses `azure-ai-inference` (`ChatCompletionsClient`); auth is `DefaultAzureCredential` in Azure, `AzureKeyCredential(AZURE_AI_FOUNDRY_API_KEY)` for local dev. The provider exposes a single `async def complete(self, *, system: str, user: str, max_input_tokens: int, max_output_tokens: int) -> LLMResult` — `LLMResult = (text, input_tokens, output_tokens, model_id)`.
3. **`curator_review` service (Task 10).** `async def execute_review_pass(*, skills, audit, blob, redis, system_state, settings, provider, actor) -> CuratorReviewRunRecord`:
   - Pause check + `redis_lock(key_curator_run_lock(), ...)` (reuses M2 lock).
   - Select candidates: `SELECT * FROM c WHERE c.status='approved' AND c.pinned=false ORDER BY c.usage.load_count DESC OFFSET 0 LIMIT @cap`. `@cap = settings.curator_review_max_skills_per_run`.
   - Filter to `agent-created` skills only — heuristic: `c.uploader LIKE 'agent:%' OR c.classification.tags ARRAY_CONTAINS 'agent-created'` (configurable; Task 2 exposes `curator_review_agent_uploader_prefix`). Pinned filter is already in the SQL; this is the safety belt.
   - For each candidate: download `published/{skill_id}/{version}/bundle.tar.gz`, extract SKILL.md text, build a deterministic input hash (sha256 of `{name}\0{version}\0{skill_md_text}`).
   - Drift pass: one `provider.complete(...)` per skill, system prompt template at `backend/services/curator_review_prompts.py:DRIFT_PROMPT`, output schema: structured JSON (verdict ∈ `{patch, keep}`, optional `patch_text`, `confidence`, `rationale`).
   - Consolidation pass: cheap pre-filter via TF-IDF cosine on SKILL.md bodies (scikit-style — implement minimal TF-IDF in pure stdlib at `backend/services/curator_review_similarity.py` to avoid a new dependency; **no sklearn**), take top-N pairs with cosine ≥ `curator_review_consolidation_min_cosine` (default 0.75), run `provider.complete` per pair, output schema includes `proposed_umbrella_skill_md` and `merged_skill_ids: [A, B]`.
   - Cost guard: track running `total_input_tokens + total_output_tokens`; if exceeds `curator_review_max_total_tokens_per_run`, set `aborted_reason="cost_cap"` and break out of the loop. Per-call breach (model returned an output larger than `max_output_tokens`) is impossible because `max_output_tokens` is passed into Foundry directly — Foundry truncates.
   - Persist each verdict as a `ReviewProposal` row (PK `/run_id`); patch & merge proposals get `status="pending"`, keep-as-is gets `status="noop"` (terminal; informational only).
   - Build a `CuratorReviewRunRecord`, persist alongside the per-run report under `{curator_reports_container}/reviews/{run_id}/`.
   - Cache invalidation NOT required — no skill state changed.
4. **Approval / rejection endpoints (Task 12).** `GET /v1/admin/curator/reviews?status=pending&run_id=...`, `GET /v1/admin/curator/reviews/{proposal_id}`, `POST /v1/admin/curator/reviews/{proposal_id}/approve`, `POST /v1/admin/curator/reviews/{proposal_id}/reject`. All gated by `require_role("admin")` (Task 13 expands to `manager` once role plumbing exists; M3 ships `admin` to match `backend/api/curator.py:60`).
5. **Apply handlers (Task 14).** `apply_patch_proposal(...)` and `apply_merge_proposal(...)` in `backend/services/curator_review_apply.py`. Each:
   - Acquires `redis_lock(key_curator_run_lock(), ...)` (mutual exclusion with deterministic curator and other review applies).
   - Re-reads the target skill doc(s); refuses if `_etag` differs from `proposal.target_etag` — sets `proposal.status="stale"`, writes audit, returns 409.
   - Calls `snapshot_svc.snapshot_published(blob, settings, run_id=f"review-apply-{proposal.id}")` for rollback safety.
   - Mutates: patch case → rebuild bundle (extract → substitute SKILL.md → `build_tar` → `put_published` → new version per `version_bump_strategy`); merge case → publish new umbrella skill (status `pending` so it goes through classifier+manager), archive the merged-in skills via the same path as M2 archive (`_copy_to_archive` from `backend/services/curator.py:158-179`, status flip via `replace_with_etag_retry`).
   - Writes audit rows (`action="patch_apply"` for patch, `action="merge_apply"` for merge — extend `AuditAction` in Task 3).
   - Sets `proposal.status="applied"`, `proposal.applied_at`, `proposal.snapshot_name`, `proposal.applied_by`.
   - Cache invalidate: `cache:skills:list:v1` + every touched `cache:skills:item:{id}`.
6. **Cost cap, run record, scheduling (Tasks 10, 11, 15).** Cost cap is enforced inside `execute_review_pass`. Run record persisted to Blob. Scheduler (Task 15) wires an optional second APScheduler job (cron from `curator_review_schedule_cron`, default `"30 3 * * *"` — 30 minutes after the deterministic pass) into `backend/workers/curator_scheduler.py`.
7. **Reports (Task 16).** Extend `backend/services/curator_report.py:render_report` with an optional `proposals_section: list[ReviewProposal] | None` argument; or add a sibling `render_review_report(run_record: CuratorReviewRunRecord) -> str`. **Decision: sibling function** so M2 reports are untouched.

## Feature Metadata

**Feature Type**: New Capability — aux-model review layer on top of the M2 curator. No breaking changes to M0/M1/M2 surfaces.
**Estimated Complexity**: High. Surface area is moderate (one service module, one apply module, one provider module, one router extension, one Cosmos container) but the cross-cutting constraints (never auto-apply, lock reuse, cost guards, AST gate extension, Foundry-only, _etag-stale handling) demand precision.
**Primary Systems Affected**:
- New: `backend/services/curator_review.py`, `backend/services/curator_review_apply.py`, `backend/services/curator_review_prompts.py`, `backend/services/curator_review_similarity.py`, `backend/services/llm/{__init__.py,provider.py,foundry.py,fake.py}`, `backend/models/review.py`.
- Updated: `backend/api/curator.py` (review endpoints), `backend/core/config.py` (auxiliary block + caps + schedule), `backend/core/cosmos.py` (new container), `backend/core/deps.py` (DI factories), `backend/core/errors.py` (new errors), `backend/models/audit.py` (new actions), `backend/services/curator_report.py` (review report renderer), `backend/workers/curator_scheduler.py` (second cron job), `backend/tests/unit/test_never_delete_invariant.py` (extend guarded list), `pyproject.toml` (azure-ai-inference dep).
**Dependencies**:
- New Python package: `azure-ai-inference>=1.0.0b6` (Foundry chat completions client).
- Already present: `azure-identity` (for `DefaultAzureCredential` in prod) — verify in `pyproject.toml`; if missing, add. `azure-core` ships with `azure-ai-inference`.
- No frontend additions in scope (M3 ships endpoints; UI plumbing for the manager review table is a follow-up).
- No new infra resources besides the Cosmos container, created on app start via `ensure_containers`.

---

## CONTEXT REFERENCES

### Relevant Codebase Files IMPORTANT: YOU MUST READ THESE FILES BEFORE IMPLEMENTING!

- `AGENTS.md` (entire file). Especially §3 (storage split), §4 (four Redis rules — proposals are a Cosmos write, never Redis-only), §5 (never-delete invariant — applies to merge handler), §8 (DI patterns). Re-read §5 before writing the merge apply path.
- `docs/PRD.md` lines 542–550 — M3 deliverables: aux-model review pass, consolidation suggestions surfaced in manager UI as actionable tickets, per-run skill cap (default 50, manager-configurable). Validation: "Manager receives 3+ actionable suggestions per run on a seeded duplicate corpus; suggestions are reviewable, dismissible, or actionable."
- `docs/PRD.md` lines 605 — Open question 7 confirms cap default of 50.
- `.agents/plans/m2-curator.md` (entire file) — Patterns M3 builds on: lock semantics, snapshot-before-mutation, audit-on-every-transition, never-delete grep gate, dry-run/real-run equivalence philosophy (M3 review pass is conceptually a permanent dry-run).
- `backend/services/curator.py` (entire file). Key sections:
  - Module docstring (lines 1–26) — template for `curator_review.py` docstring.
  - `_load_candidate_docs` (lines 144–154) — pattern for the Cosmos `SELECT` against `skills`; review uses a similar query with `pinned=false AND status='approved'` and an `ORDER BY ... LIMIT @cap`.
  - `_copy_to_archive` (lines 158–179) — exact helper the merge apply handler calls when archiving merged-in skills.
  - `_apply_one` (lines 276–347) — canonical Cosmos-first + audit + cache-bust ordering. Review-apply mirrors this exactly.
- `backend/services/snapshot.py` (entire file). The review-apply handler calls `snapshot_published(blob, settings, run_id=f"review-apply-{proposal.id}")` before any mutation (line 69 entry point).
- `backend/services/publish.py` (entire file). Lines 62–74 are the bundle rebuild pattern for the patch apply handler: extract → mutate → `build_tar` → `put_published`. Lines 88–110 are the Cosmos-first ordering.
- `backend/services/curator_state.py` (entire file). Pattern for "pause check before any work" — reuse `is_paused(...)` in `execute_review_pass`.
- `backend/services/cosmos_helpers.py` — `replace_with_etag_retry` is the only acceptable Cosmos mutate primitive for review-apply handlers.
- `backend/services/audit.py` — every state transition writes an audit row.
- `backend/api/curator.py` (entire file). Lines 57–62 show the router shape; lines 106–127 show the `run` endpoint pattern; lines 304–346 show the `status` endpoint pattern. The review endpoints (Task 12) live in this same file.
- `backend/core/redis.py` lines 56–75 — `redis_lock` context manager. Review pass and apply handlers reuse `key_curator_run_lock()` (line — added in M2; see `backend/core/redis.py` `key_curator_run_lock`).
- `backend/core/config.py` lines 90–100 — M2 curator settings block. M3 adds an "auxiliary curator review" block in the same style.
- `backend/core/cosmos.py` lines 16–72 — Container constants + `ensure_containers`. M3 adds `REVIEW_PROPOSALS_CONTAINER = "review_proposals"`.
- `backend/core/deps.py` — DI factory pattern. M3 adds `get_review_proposals_container` + `get_llm_provider`.
- `backend/core/errors.py` — `DomainError` subclass pattern. M3 adds `ReviewProposalNotFound` (404), `ReviewProposalStale` (409), `ReviewCostCapExceeded` (returned silently inside the run record — NOT raised), `LLMProviderError` (502).
- `backend/models/audit.py` lines 11–29 — Extend `AuditAction` Literal with `"patch_apply"`, `"merge_apply"`, `"review_run"`, `"review_reject"`.
- `backend/models/curator.py` (entire file) — Model style mirror for `backend/models/review.py`.
- `backend/services/skill_bundle.py` — `build_tar(files: dict[str, bytes]) -> tuple[bytes, str]` and `extract_tar(data: bytes) -> dict[str, bytes]`. Patch apply uses both.
- `backend/services/curator_report.py` — Pattern for the review report renderer.
- `backend/workers/curator_scheduler.py` — Pattern for adding a second cron job alongside the M2 deterministic pass.
- `backend/tests/unit/test_never_delete_invariant.py` lines 26–36 — `_GUARDED_FILES` list. M3 extends with the new review files.
- `backend/tests/integration/test_curator_run.py` and `test_curator_rollback_round_trip.py` — Patterns for M3 integration tests (lifespan, ASGI client, `_cleanup`, `pytestmark = pytest.mark.integration`).

### New Files to Create

- `backend/models/review.py` — `ProposalKind`, `ProposalStatus`, `ReviewProposal`, `LLMUsage`, `CuratorReviewRunRecord`, `ReviewListResponse`.
- `backend/services/llm/__init__.py` — re-exports.
- `backend/services/llm/provider.py` — `LLMProvider` ABC + `LLMResult` dataclass + `LLMProviderError`.
- `backend/services/llm/foundry.py` — `FoundryLLMProvider` (Azure AI Foundry impl via `azure-ai-inference`).
- `backend/services/llm/fake.py` — `FakeLLMProvider` for tests; constructor takes a list of canned `LLMResult` outputs.
- `backend/services/curator_review.py` — `execute_review_pass(...)` + candidate-selection helpers.
- `backend/services/curator_review_prompts.py` — `DRIFT_PROMPT`, `CONSOLIDATION_PROMPT`, output schema docs. Prompts are versioned constants (`PROMPT_VERSION = "v1"`).
- `backend/services/curator_review_similarity.py` — Stdlib TF-IDF cosine helper for consolidation pre-filter.
- `backend/services/curator_review_apply.py` — `apply_patch_proposal(...)`, `apply_merge_proposal(...)`, `reject_proposal(...)`.
- `backend/tests/unit/test_curator_review_planner.py` — Candidate selection, cost-cap math, prompt-input-hash determinism.
- `backend/tests/unit/test_curator_review_similarity.py` — TF-IDF cosine truth table.
- `backend/tests/unit/test_curator_review_proposal_model.py` — Serialization round-trip for every `ProposalKind`.
- `backend/tests/unit/test_curator_review_report.py` — Golden-file test for the review report renderer.
- `backend/tests/unit/test_llm_provider_contract.py` — `FakeLLMProvider` satisfies the ABC.
- `backend/tests/integration/test_curator_review_end_to_end.py` — Skip-if-no-emulator; full pass with `FakeLLMProvider` producing N proposals; rows present in Cosmos.
- `backend/tests/integration/test_curator_review_approve_patch.py` — Approve a `patch` proposal → bundle updated, Cosmos `_etag` advanced, audit row, snapshot present.
- `backend/tests/integration/test_curator_review_approve_merge.py` — Approve a `merge` proposal → new umbrella skill `pending`, merged-in skills `archived`, bytes copied to `archive/`.
- `backend/tests/integration/test_curator_review_reject.py` — Reject leaves Cosmos/Blob untouched; proposal `status="rejected"`.
- `backend/tests/integration/test_curator_review_lock_contention.py` — Concurrent `execute_pass` (M2) + `execute_review_pass` — one raises `LockUnavailable`.
- `backend/tests/integration/test_curator_review_cost_cap.py` — `FakeLLMProvider` returns oversized usage; pass aborts; run record reflects `aborted_reason="cost_cap"`; no proposals written.
- `backend/tests/integration/test_curator_review_stale_etag.py` — Approve after the skill's `_etag` advanced (simulating concurrent edit) → 409, proposal `status="stale"`.

### Relevant Documentation YOU SHOULD READ THESE BEFORE IMPLEMENTING!

- [Azure AI Foundry — Inference SDK overview (`azure-ai-inference`)](https://learn.microsoft.com/python/api/overview/azure/ai-inference-readme) — Why: canonical install + auth + `ChatCompletionsClient` usage. Sections: "Install the package", "Create the client" (both `AzureKeyCredential` and `DefaultAzureCredential` examples), "Get chat completions".
- [`ChatCompletionsClient` reference](https://learn.microsoft.com/python/api/azure-ai-inference/azure.ai.inference.chatcompletionsclient) — Why: parameter names (`messages`, `max_tokens`, `temperature`, `response_format`); usage object fields (`usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens`).
- [Foundry — deploy a model to an endpoint](https://learn.microsoft.com/azure/ai-foundry/how-to/deploy-models) — Why: `foundry.endpoint` and `foundry.deployment` semantics; the SDK takes `endpoint=https://{resource}.services.ai.azure.com/models` and `model=...` (or `model=deployment_name` depending on hosting style — handle both via an explicit `foundry.deployment` setting).
- [`azure-identity` `DefaultAzureCredential`](https://learn.microsoft.com/python/api/azure-identity/azure.identity.defaultazurecredential) — Why: prod auth path. Falls back through Managed Identity → CLI → env vars; we want MI in Azure.
- [Cosmos `query_items` with parameters + `OFFSET LIMIT`](https://learn.microsoft.com/azure/cosmos-db/nosql/query/offset-limit) — Why: candidate selection uses `ORDER BY c.usage.load_count DESC OFFSET 0 LIMIT @cap`.
- [Cosmos optimistic concurrency](https://learn.microsoft.com/azure/cosmos-db/nosql/database-transactions-optimistic-concurrency) — Why: review-apply handlers compare `proposal.target_etag` to current `_etag`; refuse on mismatch.
- AGENTS.md §5 — Why: read again right before writing the merge-apply handler.

### Patterns to Follow

**Module docstring (canonical: `backend/services/curator.py:1-26`).** Every new service module begins with a docstring spelling out (a) why this module exists, (b) the Cosmos-first ordering for any mutation it performs, (c) the never-delete invariant restated for this module, (d) any rule it intentionally inverts and why.

**Cosmos-first ordering for review-apply handlers (mirror `backend/services/curator.py:307-347`):**

```text
1. Lock acquisition (redis_lock on key_curator_run_lock).
2. _etag staleness check (refuse if mismatch).
3. Snapshot Blob (snapshot_svc.snapshot_published with run_id=f"review-apply-{proposal.id}").
4. Blob mutation (patch: re-upload new bundle; merge: copy bytes archive/ for merged-in, publish new umbrella).
5. Cosmos write — SOURCE OF TRUTH FLIP — via replace_with_etag_retry / create_item.
6. Audit rows (one per skill mutated).
7. Proposal update (status=applied, applied_at, applied_by, snapshot_name).
8. Redis invalidation — LAST.
```

**LLMProvider contract (new pattern):**

```python
class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_input_tokens: int,
        max_output_tokens: int,
        response_format: Literal["text", "json_object"] = "json_object",
        temperature: float = 0.0,
    ) -> LLMResult: ...

@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model_id: str
```

**Naming conventions.** Snake_case modules named for nouns (`curator_review.py`, not `do_curator_review.py`). Redis keys reuse existing helpers; no new keys in M3.

**Error handling (mirror `backend/core/errors.py:25-77`).** `ReviewProposalNotFound` (HTTP 404, `error_code="REVIEW_PROPOSAL_NOT_FOUND"`); `ReviewProposalStale` (HTTP 409, `error_code="REVIEW_PROPOSAL_STALE"`); `LLMProviderError` (HTTP 502, `error_code="LLM_PROVIDER_ERROR"`). Cost-cap is **not** an exception — it sets `aborted_reason` on the run record and the function returns normally.

**Logging (mirror `backend/services/curator.py:198`).** `bind(actor=actor, run_id=run_id, proposal_id=...)` at the top of every public service function. JSON logs carry consistent fields.

**DI (mirror `backend/api/curator.py:106-126`).** Route handlers receive `ContainerProxy` / `BlobServiceClient` / `Redis` / `LLMProvider` via `Depends(...)`; pass through to service functions.

**Test pattern (mirror `backend/tests/integration/test_curator_rollback_round_trip.py`).** ASGI client via `httpx.AsyncClient(transport=ASGITransport(app))` inside `app.router.lifespan_context(app)`; `pytestmark = pytest.mark.integration`; `_cleanup` first and last.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation (config, errors, audit actions, container, models, AST gate)

Type system, plumbing, and the safety nets first.

**Tasks:**
- Add `auxiliary.curator_review.*` settings block.
- Add `REVIEW_PROPOSALS_CONTAINER` to `backend/core/cosmos.py` with `ensure_containers` line.
- Add new `AuditAction` values.
- Add new domain errors.
- Create `backend/models/review.py`.
- Extend `_GUARDED_FILES` in `test_never_delete_invariant.py` for the new modules.
- Add `azure-ai-inference` to `pyproject.toml`.

### Phase 2: LLM Provider Layer

A single small package isolates Foundry behind a sync-style ABC; tests inject the fake.

**Tasks:**
- Create `LLMProvider` ABC + `LLMResult` + `LLMProviderError`.
- Create `FoundryLLMProvider` (Azure AI Foundry via `azure-ai-inference`; auth via `DefaultAzureCredential` with `AzureKeyCredential` fallback).
- Create `FakeLLMProvider`.
- Wire DI factory `get_llm_provider(settings)` that returns Foundry in prod, fake when `AUXILIARY_CURATOR_REVIEW_PROVIDER=fake` (test-only override).

### Phase 3: Review Service (planner + executor)

**Tasks:**
- Prompt templates + versioning (`curator_review_prompts.py`).
- TF-IDF cosine pre-filter (`curator_review_similarity.py`).
- `execute_review_pass(...)` — lock, pause check, candidate selection, drift loop, consolidation loop, cost guard, proposal persistence, run record persistence.

### Phase 4: Approval / Apply Layer

**Tasks:**
- `apply_patch_proposal(...)` (lock → etag check → snapshot → bundle rebuild → publish → cosmos → audit → cache bust).
- `apply_merge_proposal(...)` (lock → etag check (all merged-in + umbrella draft) → snapshot → publish umbrella as `pending` → archive merged-in via Blob copy + Cosmos status flip + audit per skill → cache bust).
- `reject_proposal(...)` (Cosmos update + audit; no Blob touch).
- API endpoints in `backend/api/curator.py`.

### Phase 5: Reports + Scheduling + Tests + Docs

**Tasks:**
- `render_review_report(...)` + persistence.
- Second APScheduler job in `backend/workers/curator_scheduler.py`.
- Unit + integration tests.
- Extend AST gate; verify it fails when a `delete_*` is sneaked in.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### Task 1: UPDATE `pyproject.toml`

- **ADD**: `"azure-ai-inference>=1.0.0b6"` to `[project] dependencies`. Verify `"azure-identity"` is already present; if not, add `"azure-identity>=1.17"`.
- **PATTERN**: Mirror existing additions made for M2 (`apscheduler`).
- **GOTCHA**: `azure-ai-inference` is in beta; pin to `>=1.0.0b6,<2`. Confirm version available on PyPI at implementation time and adjust.
- **VALIDATE**: `uv sync && uv run python -c "from azure.ai.inference.aio import ChatCompletionsClient; print('ok')"`

### Task 2: UPDATE `backend/core/config.py`

- **ADD** at the end of the `Settings` class (after the M2 `# ---- Curator (M2) ----` block):

  ```python
  # ---- Aux model: curator review (M3) ----
  # Provider toggle (only "foundry" or test-only "fake" supported).
  curator_review_provider: Literal["foundry", "fake"] = "foundry"

  # Azure AI Foundry endpoint config.
  foundry_endpoint: str = ""             # e.g. "https://my-foundry.services.ai.azure.com/models"
  foundry_deployment: str = ""           # deployment name or model id
  foundry_api_version: str = "2024-08-01-preview"

  # Auth: prefer Managed Identity in Azure; fall back to API key for local dev only.
  azure_ai_foundry_api_key: str = ""

  # Per-call token caps (passed to the Foundry SDK; truncation happens at the model).
  curator_review_max_input_tokens: int = 6000
  curator_review_max_output_tokens: int = 1500

  # Per-run hard caps. Breach => abort + record aborted_reason="cost_cap".
  curator_review_max_skills_per_run: int = 50
  curator_review_max_total_tokens_per_run: int = 400_000

  # Candidate filter knobs.
  curator_review_agent_uploader_prefix: str = "agent:"
  curator_review_consolidation_min_cosine: float = 0.75
  curator_review_consolidation_max_pairs: int = 20

  # Schedule for the optional second cron job.
  curator_review_schedule_cron: str = "30 3 * * *"
  curator_review_enabled: bool = False  # off by default; enable per-env.
  ```

- **PATTERN**: Mirror the M2 curator block at `backend/core/config.py:90-100`.
- **GOTCHA**: `Literal` must include `"fake"` because tests set `CURATOR_REVIEW_PROVIDER=fake`. Document that `fake` is test-only.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_config.py -v` (extend with one assertion that defaults parse cleanly).

### Task 3: UPDATE `backend/models/audit.py`

- **ADD** to the `AuditAction` `Literal`: `"patch_apply"`, `"merge_apply"`, `"review_run"`, `"review_reject"`.
- **PATTERN**: Mirror existing additions (e.g. `"stale"` added in M2).
- **VALIDATE**: `uv run pytest backend/tests/unit/test_models.py -v` after extending.

### Task 4: UPDATE `backend/core/errors.py`

- **ADD**: `ReviewProposalNotFound` (404, `"REVIEW_PROPOSAL_NOT_FOUND"`), `ReviewProposalStale` (409, `"REVIEW_PROPOSAL_STALE"`), `LLMProviderError` (502, `"LLM_PROVIDER_ERROR"`).
- **PATTERN**: Mirror `backend/core/errors.py:25-77`.
- **GOTCHA**: **Do NOT** add a `ReviewCostCapExceeded` exception. Cost-cap is communicated via the run record, never raised.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_errors.py -v` (extend with the three new codes).

### Task 5: UPDATE `backend/core/cosmos.py`

- **ADD**: `REVIEW_PROPOSALS_CONTAINER = "review_proposals"` near the other container constants.
- **ADD** inside `ensure_containers`:

  ```python
  await db.create_container_if_not_exists(
      id=REVIEW_PROPOSALS_CONTAINER,
      partition_key=PartitionKey(path="/run_id"),
  )
  ```

- **PATTERN**: Mirror lines 47-71.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_cosmos_bootstrap.py -v`

### Task 6: UPDATE `backend/tests/unit/test_never_delete_invariant.py`

- **ADD** to `_GUARDED_FILES`:
  - `"backend/services/curator_review.py"`
  - `"backend/services/curator_review_apply.py"`
  - `"backend/services/curator_review_prompts.py"`
  - `"backend/services/curator_review_similarity.py"`
  - `"backend/services/llm/foundry.py"`
  - `"backend/services/llm/fake.py"`
  - `"backend/services/llm/provider.py"`
- **PATTERN**: Mirror existing list (lines 26-36).
- **GOTCHA**: Test runs `path.exists()` and asserts; create empty placeholder files in Task 7+ before this test runs against new files, or temporarily wrap in `if path.exists()` and tighten once files exist. **Decision**: ship the test extension in the same PR as the file creation; ensure all files exist by end of Task 14.
- **VALIDATE**: After Task 14: `uv run pytest backend/tests/unit/test_never_delete_invariant.py -v`

### Task 7: CREATE `backend/models/review.py`

- **IMPLEMENT**:

  ```python
  """M3 — LLM review proposal models."""
  from __future__ import annotations
  import uuid
  from datetime import UTC, datetime
  from typing import Any, Literal
  from pydantic import BaseModel, Field

  ProposalKind = Literal["patch", "merge", "keep"]
  ProposalStatus = Literal["pending", "approved", "applied", "rejected", "stale", "noop"]

  def _utc_now() -> datetime: return datetime.now(UTC)

  class LLMUsage(BaseModel):
      input_tokens: int = 0
      output_tokens: int = 0
      model_id: str = ""
      prompt_version: str = "v1"

  class PatchPayload(BaseModel):
      target_skill_id: str
      target_version: str
      patch_text: str  # unified-diff text or full SKILL.md replacement
      replacement_mode: Literal["unified_diff", "full_replace"] = "full_replace"
      rationale: str = ""

  class MergePayload(BaseModel):
      merged_skill_ids: list[str]  # >= 2
      proposed_umbrella_name: str
      proposed_umbrella_version: str = "1.0.0"
      proposed_umbrella_skill_md: str
      rationale: str = ""

  class KeepPayload(BaseModel):
      target_skill_id: str
      rationale: str = ""

  class ReviewProposal(BaseModel):
      """One row in the `review_proposals` Cosmos container (PK /run_id)."""
      id: str = Field(default_factory=lambda: uuid.uuid4().hex)
      run_id: str
      kind: ProposalKind
      status: ProposalStatus = "pending"
      created_at: datetime = Field(default_factory=_utc_now)
      created_by: str = "system:curator_review"

      # Snapshot of the inputs the model saw (used by stale-etag check on apply).
      target_skill_ids: list[str] = Field(default_factory=list)
      target_etags: dict[str, str] = Field(default_factory=dict)  # skill_id -> _etag
      input_hash: str = ""  # sha256 of (name||version||skill_md_text) concatenated for all inputs

      # Exactly one of the following is set, by `kind`.
      patch: PatchPayload | None = None
      merge: MergePayload | None = None
      keep: KeepPayload | None = None

      # LLM telemetry.
      usage: LLMUsage = Field(default_factory=LLMUsage)
      confidence: float = 0.0  # 0..1 if model returns; else 0

      # Apply / reject lifecycle.
      approved_by: str | None = None
      approved_at: datetime | None = None
      applied_by: str | None = None
      applied_at: datetime | None = None
      rejected_by: str | None = None
      rejected_at: datetime | None = None
      rejection_reason: str | None = None
      snapshot_name: str | None = None
      apply_error: str | None = None

  class CuratorReviewRunRecord(BaseModel):
      run_id: str
      started_at: datetime
      finished_at: datetime
      candidates_considered: int = 0
      proposals_emitted: int = 0
      proposals_by_kind: dict[ProposalKind, int] = Field(
          default_factory=lambda: {"patch": 0, "merge": 0, "keep": 0}
      )
      total_input_tokens: int = 0
      total_output_tokens: int = 0
      provider: str = "foundry"
      model_id: str = ""
      prompt_version: str = "v1"
      aborted_reason: Literal["cost_cap", "lock", "paused", "provider_error", None] = None
      lock_token: str | None = None

  class ReviewListResponse(BaseModel):
      proposals: list[ReviewProposal]
      total: int
  ```

- **PATTERN**: Mirror `backend/models/curator.py`.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_curator_review_proposal_model.py -v` (created in Task 22).

### Task 8: CREATE `backend/services/llm/provider.py`

- **IMPLEMENT**:

  ```python
  """LLMProvider ABC + LLMResult.

  M3 has exactly two implementations: FoundryLLMProvider (prod + dev) and
  FakeLLMProvider (tests). Do NOT add OpenAI/Anthropic providers.
  """
  from __future__ import annotations
  from abc import ABC, abstractmethod
  from dataclasses import dataclass
  from typing import Literal

  @dataclass(frozen=True, slots=True)
  class LLMResult:
      text: str
      input_tokens: int
      output_tokens: int
      model_id: str

  class LLMProvider(ABC):
      @abstractmethod
      async def complete(
          self,
          *,
          system: str,
          user: str,
          max_input_tokens: int,
          max_output_tokens: int,
          response_format: Literal["text", "json_object"] = "json_object",
          temperature: float = 0.0,
      ) -> LLMResult: ...
  ```

- **PATTERN**: Minimal; defines the contract only.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_llm_provider_contract.py -v`

### Task 9: CREATE `backend/services/llm/foundry.py`

- **IMPLEMENT**: `FoundryLLMProvider` constructed from `Settings`. Lazily builds a `ChatCompletionsClient` from `azure.ai.inference.aio`. Credential resolution: if `settings.azure_ai_foundry_api_key` is non-empty → `AzureKeyCredential(...)` (local dev); else → `DefaultAzureCredential()` (prod, expects Managed Identity).
  ```python
  from azure.ai.inference.aio import ChatCompletionsClient
  from azure.ai.inference.models import SystemMessage, UserMessage
  from azure.core.credentials import AzureKeyCredential
  from azure.identity.aio import DefaultAzureCredential
  ```
  `complete()` calls `client.complete(messages=[SystemMessage(content=system), UserMessage(content=user)], max_tokens=max_output_tokens, temperature=temperature, model=settings.foundry_deployment, response_format={"type": "json_object"} if response_format=="json_object" else None)`. Map response → `LLMResult(text=resp.choices[0].message.content, input_tokens=resp.usage.prompt_tokens, output_tokens=resp.usage.completion_tokens, model_id=resp.model or settings.foundry_deployment)`. Wrap any SDK exception in `LLMProviderError`.
- **IMPORTS**: As above.
- **GOTCHA**: `max_input_tokens` is informational only — Foundry does not accept it directly. Enforce it client-side: if `len(user_tokens_estimate) > max_input_tokens` (rough char-based estimate `len(user)//4`), truncate `user` and prepend a `[truncated for length]` marker.
- **GOTCHA**: The Foundry endpoint format is `https://{resource}.services.ai.azure.com/models`. The SDK sometimes wants the model name as `model=` and sometimes as a deployment in the URL. Use the `model=settings.foundry_deployment` argument and the `endpoint=settings.foundry_endpoint` constructor arg. Add a 30s `aiohttp` timeout.
- **VALIDATE**: Unit test: instantiate with `azure_ai_foundry_api_key="dummy"`, mock the underlying client, assert `complete()` returns an `LLMResult` shape; assert credential selection picks `AzureKeyCredential` when api_key set.

### Task 10: CREATE `backend/services/llm/fake.py`

- **IMPLEMENT**:

  ```python
  from collections import deque
  from .provider import LLMProvider, LLMResult

  class FakeLLMProvider(LLMProvider):
      """Returns canned LLMResults in order. Test-only."""
      def __init__(self, canned: list[LLMResult]):
          self._q = deque(canned)
          self.calls: list[dict] = []

      async def complete(self, **kwargs) -> LLMResult:
          self.calls.append(kwargs)
          if not self._q:
              raise RuntimeError("FakeLLMProvider exhausted")
          return self._q.popleft()
  ```

- **VALIDATE**: `uv run pytest backend/tests/unit/test_llm_provider_contract.py -v`

### Task 11: CREATE `backend/services/llm/__init__.py`

- **IMPLEMENT**:

  ```python
  from .provider import LLMProvider, LLMResult, LLMProviderError
  from .foundry import FoundryLLMProvider
  from .fake import FakeLLMProvider
  __all__ = ["LLMProvider", "LLMResult", "LLMProviderError",
             "FoundryLLMProvider", "FakeLLMProvider"]
  ```

### Task 12: UPDATE `backend/core/errors.py` (re-export)

- Ensure `LLMProviderError` is importable both from `backend.core.errors` (HTTP mapping) and from `backend.services.llm`. Implementation choice: define in `backend/core/errors.py` and re-export from `backend/services/llm/provider.py` via `from backend.core.errors import LLMProviderError`. This keeps the FastAPI exception handler wiring untouched.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_errors.py -v`

### Task 13: UPDATE `backend/core/deps.py`

- **ADD**:

  ```python
  def get_review_proposals_container(
      db: DatabaseProxy = Depends(get_db),
  ) -> ContainerProxy:
      return get_container(db, REVIEW_PROPOSALS_CONTAINER)

  @lru_cache(maxsize=1)
  def _llm_provider_singleton(settings: Settings) -> LLMProvider:
      if settings.curator_review_provider == "fake":
          return FakeLLMProvider(canned=[])  # tests override via DI
      return FoundryLLMProvider(settings)

  def get_llm_provider(settings: Settings = Depends(settings_dep)) -> LLMProvider:
      return _llm_provider_singleton(settings)
  ```

- **PATTERN**: Mirror existing factories (e.g. `get_system_state_container`).
- **GOTCHA**: `FakeLLMProvider(canned=[])` is a placeholder; integration tests should override the dependency with `app.dependency_overrides[get_llm_provider] = lambda: FakeLLMProvider(canned=[...])`. Document this.
- **VALIDATE**: Endpoint smoke test in Task 25.

### Task 14: CREATE `backend/services/curator_review_prompts.py`

- **IMPLEMENT**: Versioned string constants.

  ```python
  PROMPT_VERSION = "v1"

  DRIFT_SYSTEM = "You are a meticulous reviewer of agent skill definitions..."
  DRIFT_USER_TEMPLATE = """\
  Skill name: {name}
  Skill version: {version}
  Current SKILL.md:
  ---
  {skill_md}
  ---
  Decide: verdict ∈ {{"keep", "patch"}}.
  Return JSON: {{"verdict": "...", "patch_text": "<full replacement SKILL.md>", "confidence": 0..1, "rationale": "..."}}.
  Only return JSON.
  """

  CONSOLIDATION_SYSTEM = "..."
  CONSOLIDATION_USER_TEMPLATE = """\
  Skill A name: {a_name}\nA SKILL.md:\n---\n{a_md}\n---\n
  Skill B name: {b_name}\nB SKILL.md:\n---\n{b_md}\n---\n
  Decide: verdict ∈ {{"keep", "merge"}}. If merge, propose an umbrella SKILL.md combining both.
  Return JSON: {{"verdict": "...", "umbrella_name": "...", "umbrella_skill_md": "...", "confidence": 0..1, "rationale": "..."}}.
  Only return JSON.
  """
  ```

- **VALIDATE**: Golden test asserts `PROMPT_VERSION == "v1"`.

### Task 15: CREATE `backend/services/curator_review_similarity.py`

- **IMPLEMENT**: Pure-stdlib TF-IDF cosine.

  ```python
  import math, re
  from collections import Counter

  _WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")

  def _tokenize(text: str) -> list[str]:
      return [t.lower() for t in _WORD.findall(text)]

  def top_similar_pairs(
      docs: dict[str, str], *, min_cosine: float, max_pairs: int
  ) -> list[tuple[str, str, float]]:
      """Return [(id_a, id_b, cosine), ...] sorted desc, filtered by min_cosine."""
      tokens = {k: _tokenize(v) for k, v in docs.items()}
      df: Counter[str] = Counter()
      for toks in tokens.values():
          df.update(set(toks))
      N = max(1, len(docs))
      idf = {w: math.log((1 + N) / (1 + c)) + 1 for w, c in df.items()}
      vecs: dict[str, dict[str, float]] = {}
      for k, toks in tokens.items():
          tf = Counter(toks)
          v = {w: (tf[w] / max(1, len(toks))) * idf[w] for w in tf}
          norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
          vecs[k] = {w: x / norm for w, x in v.items()}
      keys = list(vecs.keys())
      pairs: list[tuple[str, str, float]] = []
      for i in range(len(keys)):
          for j in range(i + 1, len(keys)):
              a, b = vecs[keys[i]], vecs[keys[j]]
              # dot product on shorter dict
              short, long = (a, b) if len(a) <= len(b) else (b, a)
              cos = sum(short[w] * long.get(w, 0.0) for w in short)
              if cos >= min_cosine:
                  pairs.append((keys[i], keys[j], cos))
      pairs.sort(key=lambda t: t[2], reverse=True)
      return pairs[:max_pairs]
  ```

- **GOTCHA**: O(N²) over candidate pairs; fine for N=50.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_curator_review_similarity.py -v`

### Task 16: CREATE `backend/services/curator_review.py`

- **IMPLEMENT**: Module docstring (mirror `backend/services/curator.py:1-26`) explicitly stating "this module emits no mutations; proposals require manager approval to apply".

  Public API:

  ```python
  async def execute_review_pass(
      *,
      provider: LLMProvider,
      skills: ContainerProxy,
      audit: ContainerProxy,
      review_proposals: ContainerProxy,
      system_state: ContainerProxy,
      blob: BlobServiceClient,
      redis: Redis,
      settings: Settings,
      now: datetime | None = None,
      actor: str = "system:curator_review",
  ) -> CuratorReviewRunRecord: ...
  ```

  Algorithm:
  1. `bind(actor=actor, run_id=run_id)`; `run_id = _utc_iso_compact(now)`; `started_at = now()`.
  2. If `await curator_state.is_paused(...)` → return `CuratorReviewRunRecord(..., aborted_reason="paused")`.
  3. Acquire `redis_lock(redis, key_curator_run_lock(), ttl=settings.curator_lock_ttl_seconds)`. On `LockUnavailable` → return record with `aborted_reason="lock"`. (Do not raise; review is opportunistic.)
  4. Query candidates: `SELECT * FROM c WHERE c.status='approved' AND c.pinned=false ORDER BY c.usage.load_count DESC OFFSET 0 LIMIT @cap` with `@cap = settings.curator_review_max_skills_per_run`. Filter to `c.uploader STARTSWITH settings.curator_review_agent_uploader_prefix` (server-side in SQL via `STARTSWITH(c.uploader, @prefix)`).
  5. For each candidate: download `published_blob_path(skill_id, version)` from `blob_published_container`; extract `SKILL.md` from the tar via `extract_tar(...)`. Skip the candidate (warn-log) if SKILL.md is missing.
  6. **Drift pass**: for each candidate, call `provider.complete(system=DRIFT_SYSTEM, user=DRIFT_USER_TEMPLATE.format(...), max_input_tokens=..., max_output_tokens=...)`. Parse JSON; on parse error → `LLMProviderError`-style record but proposal is just dropped (log warn). Cost guard: accumulate tokens; if `total > max_total_tokens_per_run`, break loop, set `aborted_reason="cost_cap"`.
  7. Persist proposal: if `verdict == "patch"` → `ReviewProposal(kind="patch", status="pending", patch=PatchPayload(...))` with `target_etags={skill_id: doc._etag}`. If `verdict == "keep"` → `ReviewProposal(kind="keep", status="noop", keep=KeepPayload(...))`. Insert via `review_proposals.create_item(...)`.
  8. **Consolidation pass**: build `docs = {skill_id: skill_md_text}` from candidates whose drift verdict was `keep` (don't merge skills already flagged for patch). Use `top_similar_pairs(docs, min_cosine=..., max_pairs=...)`. For each `(a, b, cos)`: call `provider.complete(...)`; if `verdict == "merge"` → `ReviewProposal(kind="merge", ..., merge=MergePayload(merged_skill_ids=[a, b], ...))` with `target_etags={a: etag_a, b: etag_b}`.
  9. Persist `CuratorReviewRunRecord` to `{curator_reports_container}/reviews/{run_id}/run.json` + `REPORT.md`.
  10. Return record. (No cache invalidation — no skill state changed.)

- **GOTCHA**: `_etag` is on each Cosmos doc as `"_etag"`. Capture it from the raw dict before `SkillDoc.model_validate(...)` strips it.
- **GOTCHA**: Cost cap is checked AFTER each provider call (so the call that breached still gets recorded). Set `aborted_reason="cost_cap"` and break.
- **GOTCHA**: Persist proposals as you go, NOT in a final batch. A partial run is still useful to managers, and Cosmos partial-success is fine because each proposal is independent.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_review_end_to_end.py -v`

### Task 17: CREATE `backend/services/curator_review_apply.py`

- **IMPLEMENT**: Module docstring. Three public functions:

  ```python
  async def apply_patch_proposal(*, proposal_id: str, ...) -> ReviewProposal
  async def apply_merge_proposal(*, proposal_id: str, ...) -> ReviewProposal
  async def reject_proposal(*, proposal_id: str, actor: str, reason: str, ...) -> ReviewProposal
  ```

  Common shape for the two `apply_*` functions:

  1. Look up proposal by `id`. Since PK is `/run_id` and we have only `id`, do a cross-partition query `SELECT * FROM c WHERE c.id=@id` (rare op; acceptable). Alternative: include `run_id` in the URL — **decision: include `run_id` query param on the endpoint** (`POST /reviews/{proposal_id}/approve?run_id=...`), avoiding cross-partition. Update endpoint signatures accordingly.
  2. Refuse if `proposal.status != "pending"` → `ReviewProposalStale` if `applied`/`rejected`, else 409 with code `REVIEW_PROPOSAL_NOT_PENDING`.
  3. Acquire `redis_lock(redis, key_curator_run_lock(), ttl=settings.curator_lock_ttl_seconds)`. On contention → return 423 (mirror M2 behaviour — re-raise `LockUnavailable`).
  4. Re-read each `target_skill_id`; compare `_etag` to `proposal.target_etags[skill_id]`. On mismatch: set `proposal.status="stale"`; persist; raise `ReviewProposalStale`.
  5. Snapshot: `manifest = await snapshot_svc.snapshot_published(blob, settings, run_id=f"review-apply-{proposal_id}")`. Store `proposal.snapshot_name = manifest.run_id`.
  6. Mutate (patch or merge — see below).
  7. Audit row per mutated skill.
  8. Update proposal: `status="applied"`, `applied_by=actor`, `applied_at=now()`.
  9. Cache invalidate: `redis.delete(key_cache_list(), *[key_cache_item(s) for s in touched])`.
  10. Return updated proposal.

  **Patch mutation** (`apply_patch_proposal`):
  - Download current bundle from `published/`.
  - `files = extract_tar(data)`; `files["SKILL.md"] = proposal.patch.patch_text.encode("utf-8")`.
  - `tar_bytes, checksum = build_tar(files)`.
  - Bump version: `new_version = _bump_patch_version(doc.version)` (simple `1.2.3 → 1.2.4`; if not semver, append `+rev{N}`).
  - `await put_published(blob, settings, skill_id=doc.skill_id, version=new_version, data=tar_bytes)`.
  - Update Cosmos doc via `replace_with_etag_retry`: set `version=new_version`, `bundle=Bundle(...)`, `approved_at=now()`, `approver=actor`, leave `status="approved"`. **Do not** delete or modify the previous version's blob (never-delete).
  - Audit `action="patch_apply"`, `before={"version": old_version}`, `after={"version": new_version, "checksum": checksum}`, `metadata={"proposal_id": proposal_id}`.

  **Merge mutation** (`apply_merge_proposal`):
  - Create new umbrella skill doc: `umbrella_id = f"merge-{utc-iso-compact()}-{shortuuid}"`. Status `pending` (goes through classifier + manager approval like any new skill). Build bundle with just `SKILL.md` from `proposal.merge.proposed_umbrella_skill_md` and any auxiliary files copied from skill A (decision: M3 only copies SKILL.md; references/templates from sources are NOT auto-merged — managers re-bundle later if needed).
  - `await skills.create_item(...)` for umbrella; `await put_published(...)` for umbrella bundle.
  - For each `merged_skill_id`: `await _copy_to_archive(blob, settings, ...)` (import from `backend/services/curator.py`); `replace_with_etag_retry` to set `status="archived"`.
  - Audit per merged skill: `action="merge_apply"`, `metadata={"proposal_id": proposal_id, "umbrella_id": umbrella_id}`.
  - Audit for umbrella: `action="upload"` with metadata signalling `created_by_merge=True`.

  **Reject** (`reject_proposal`):
  - Set `status="rejected"`, `rejected_by`, `rejected_at`, `rejection_reason`. No lock, no snapshot, no blob touch.
  - Audit `action="review_reject"`, `skill_id=proposal.target_skill_ids[0]` (use first target), `metadata={"proposal_id": proposal_id, "kind": proposal.kind}`.

- **GOTCHA**: Imports from `backend/services/curator.py:_copy_to_archive` — that helper is private. Either (a) make it public (`copy_to_archive`) or (b) re-implement locally. **Decision: rename to `copy_published_to_archive` and export.** Update Task 17 sub-step + `backend/services/curator.py` accordingly.
- **GOTCHA**: Merge handler creates a brand-new skill. The classifier worker (M0) will pick it up via the upload flow. Make sure to push the new `umbrella_id` onto `key_queue_classifier()` so it gets classified (mirror `backend/services/upload.py`'s queue push). Alternatively, set `classifier_status="done"` with a placeholder classification — **decision: enqueue properly** so the umbrella goes through the standard pipeline.
- **GOTCHA**: AST gate forbids `delete_item` / `delete_blob` in this module. Verified by Task 6.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_review_approve_{patch,merge}.py backend/tests/integration/test_curator_review_reject.py backend/tests/integration/test_curator_review_stale_etag.py -v`

### Task 18: UPDATE `backend/services/curator.py`

- **REFACTOR**: Rename `_copy_to_archive` → `copy_published_to_archive` (keep the `_` alias for backward-compat or just update the one call site in `_apply_one`).
- **PATTERN**: Leaves behaviour untouched; public symbol re-used by review apply.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_run.py -v`

### Task 19: CREATE `backend/services/curator_review_report.py`

- **IMPLEMENT**:

  ```python
  def render_review_report(rec: CuratorReviewRunRecord, proposals: list[ReviewProposal]) -> str: ...
  async def persist_review_report(blob, settings, rec, proposals) -> None: ...
  ```

  Writes `{curator_reports_container}/reviews/{run_id}/run.json` and `REPORT.md`. Report sections: header (run id, started/finished, provider, model_id, prompt_version), summary (candidates_considered, proposals_emitted, by_kind, token totals, aborted_reason), proposal table.

- **PATTERN**: Mirror `backend/services/curator_report.py`.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_curator_review_report.py -v`

### Task 20: UPDATE `backend/api/curator.py`

- **ADD** endpoints:

  ```python
  @router.post("/review", response_model=CuratorReviewRunRecord)
  async def run_review(
      user: User = Depends(_require_admin),
      settings: Settings = Depends(settings_dep),
      provider: LLMProvider = Depends(get_llm_provider),
      skills: ContainerProxy = Depends(get_skills_container),
      audit: ContainerProxy = Depends(get_audit_container),
      review_proposals: ContainerProxy = Depends(get_review_proposals_container),
      system_state: ContainerProxy = Depends(get_system_state_container),
      blob: BlobServiceClient = Depends(get_blob),
      redis: Redis = Depends(get_redis_client),
  ) -> CuratorReviewRunRecord: ...

  @router.get("/reviews", response_model=ReviewListResponse)
  async def list_reviews(
      status: str | None = Query(None),
      run_id: str | None = Query(None),
      limit: int = Query(100, le=500),
      user: User = Depends(_require_admin),
      review_proposals: ContainerProxy = Depends(get_review_proposals_container),
  ) -> ReviewListResponse: ...

  @router.get("/reviews/{proposal_id}", response_model=ReviewProposal)
  async def get_review(
      proposal_id: str,
      run_id: str = Query(...),
      ...
  ) -> ReviewProposal: ...

  @router.post("/reviews/{proposal_id}/approve", response_model=ReviewProposal)
  async def approve_review(
      proposal_id: str,
      run_id: str = Query(...),
      ...
  ) -> ReviewProposal:
      # Loads proposal, dispatches to apply_patch_proposal or apply_merge_proposal by kind.
      ...

  @router.post("/reviews/{proposal_id}/reject", response_model=ReviewProposal)
  async def reject_review(
      proposal_id: str,
      run_id: str = Query(...),
      reason: str = Query(""),
      ...
  ) -> ReviewProposal: ...
  ```

- **PATTERN**: Mirror existing `run`/`rollback` handlers in the same file (lines 106-148).
- **GOTCHA**: `run_id` as a query param avoids cross-partition reads on the `review_proposals` container.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_review_end_to_end.py -v`

### Task 21: UPDATE `backend/workers/curator_scheduler.py`

- **ADD**: If `settings.curator_review_enabled` is `True`, schedule a second cron job using `settings.curator_review_schedule_cron` that calls `curator_review.execute_review_pass(...)`.
- **PATTERN**: Mirror the existing M2 cron job. Use the same `AsyncIOScheduler` instance.
- **GOTCHA**: Provider construction at scheduler boot: use `FoundryLLMProvider(settings)` directly (no DI). If construction fails (missing endpoint), log error and skip scheduling — do not crash the scheduler.
- **VALIDATE**: `python -m backend.workers.curator_scheduler --help` runs; with `CURATOR_REVIEW_ENABLED=true` and a valid Foundry endpoint, the second cron job is registered (visible in log lines).

### Task 22: CREATE unit tests

- `backend/tests/unit/test_curator_review_proposal_model.py` — Round-trip every `ProposalKind` variant; assert `_utc_now` defaults; assert `target_etags` dict round-trips.
- `backend/tests/unit/test_llm_provider_contract.py` — `FakeLLMProvider` returns canned results in order; raises on exhaustion; records calls.
- `backend/tests/unit/test_curator_review_similarity.py` — Two identical docs → cosine 1.0; orthogonal token sets → cosine 0; pair filtering by `min_cosine`; max_pairs cap.
- `backend/tests/unit/test_curator_review_report.py` — Golden file; update via `UPDATE_GOLDEN=1`.
- `backend/tests/unit/test_curator_review_planner.py` — Construct fake docs + `FakeLLMProvider` with canned drift/merge responses; assert candidate filtering (pinned excluded, status filter), input_hash determinism (same inputs → same hash), cost-cap math (after K canned responses summing to `> max_total_tokens`, abort and break).
- **VALIDATE**: `uv run pytest backend/tests/unit -v`

### Task 23: CREATE integration test — end-to-end

- `backend/tests/integration/test_curator_review_end_to_end.py`:
  1. `_cleanup()`.
  2. Seed 5 approved, unpinned skills with `uploader="agent:test"`. Publish bundles (real tar via `build_tar`) with distinct SKILL.md bodies; pair `(s0, s1)` is nearly identical (for consolidation).
  3. Override `get_llm_provider` with `FakeLLMProvider` whose canned responses are: 5 drift verdicts (3 keep, 2 patch with `patch_text`), 1 consolidation verdict (`merge` for the s0/s1 pair).
  4. `POST /v1/admin/curator/review`.
  5. Assert response: `proposals_emitted == 3` (2 patch + 1 merge; `keep` is `noop` and counted separately).
  6. Query `review_proposals` container, partition by `run_id`; assert 6 rows total (3 keep noop + 2 patch pending + 1 merge pending).
  7. Assert report blob exists at `curator/reviews/{run_id}/run.json`.
- `pytestmark = pytest.mark.integration`.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_review_end_to_end.py -v`

### Task 24: CREATE integration tests — apply paths

- `test_curator_review_approve_patch.py`: Seed; create pending patch proposal directly in Cosmos (skip review-run); `POST /reviews/{id}/approve?run_id=...`; assert (a) new bundle in `published/` with new version; (b) Cosmos doc updated; (c) old version's blob still present (never-delete); (d) audit row with `action="patch_apply"`; (e) snapshot folder `review-apply-{id}` present; (f) proposal `status="applied"`.
- `test_curator_review_approve_merge.py`: Seed two skills A, B; pending merge proposal targeting both; approve; assert (a) new umbrella skill in `skills` with `status="pending"` and bundle in `published/`; (b) classifier queue contains umbrella_id; (c) A and B both `status="archived"` with bytes in `archive/` and source still in `published/`; (d) audit rows for both archives + umbrella upload; (e) proposal `status="applied"`.
- `test_curator_review_reject.py`: Pending proposal; `POST /reviews/{id}/reject?reason=...`; assert proposal `status="rejected"`; no Blob or Cosmos `skills` mutation; audit row with `action="review_reject"`.
- `test_curator_review_stale_etag.py`: Pending patch proposal with captured etag; mutate the skill out-of-band (`replace_with_etag_retry` flipping pinned True); approve → 409 `REVIEW_PROPOSAL_STALE`; proposal `status="stale"`.
- **VALIDATE**: `uv run pytest backend/tests/integration -v -m integration -k curator_review_approve`

### Task 25: CREATE integration test — lock contention

- `test_curator_review_lock_contention.py`: Start two `execute_review_pass` (or one M2 `execute_pass` + one `execute_review_pass`) tasks concurrently via `asyncio.gather`. Provide `FakeLLMProvider` with slow responses (small `asyncio.sleep`) so the first holds the lock. Assert second returns a record with `aborted_reason="lock"` (review pass) — or for the mixed case, one task raises `LockUnavailable` and the other completes.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_review_lock_contention.py -v`

### Task 26: CREATE integration test — cost cap

- `test_curator_review_cost_cap.py`: `FakeLLMProvider` returns `LLMResult(input_tokens=100_000, output_tokens=100_000, ...)`. Set `curator_review_max_total_tokens_per_run=150_000`. Seed 5 candidates. Run review. Assert: second call breaches cap; loop aborts; record `aborted_reason="cost_cap"`; only 1 proposal persisted (the first call's verdict).
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_review_cost_cap.py -v`

### Task 27: VERIFY AST gate

- After Tasks 7-21 land, run: `uv run pytest backend/tests/unit/test_never_delete_invariant.py -v`. Must pass for all 16 guarded files.
- As a regression check, temporarily insert `await skills.delete_item(...)` into `curator_review_apply.py`, run the test, confirm it fails, then remove.
- **VALIDATE**: Tests pass after revert.

### Task 28: UPDATE `backend/app.py` (or wherever the router is mounted)

- No changes needed to router include (review endpoints are on the same `curator_router`). Confirm `include_router(curator_router.router)` is already present (M2).
- **VALIDATE**: Existing M2 test `test_curator_endpoints.py` still passes.

### Task 29: UPDATE `AGENTS.md` §5

- **ADD** a short paragraph: "M3 introduces an LLM review pass (`backend/services/curator_review.py`) that emits *proposals* to a `review_proposals` Cosmos container. Proposals require manager approval to apply; there is no auto-apply code path. The never-delete AST gate has been extended to cover the new review modules. The aux model is Azure AI Foundry only — adding any other provider (OpenAI/Anthropic/etc.) requires a follow-up RFC."
- **UPDATE** the "Key Files" table to add `backend/services/curator_review.py` and `backend/services/curator_review_apply.py`.
- **VALIDATE**: Manual review.

### Task 30: UPDATE `docs/PRD.md` (optional, lightweight)

- Add a one-line note under M3 acceptance: "Validated by `test_curator_review_end_to_end.py` + `test_curator_review_approve_{patch,merge}.py`."
- **VALIDATE**: Manual review.

---

## TESTING STRATEGY

### Unit Tests

In `backend/tests/unit/`. Pure-function coverage:

- `test_curator_review_proposal_model.py` — every `ProposalKind` round-trips.
- `test_llm_provider_contract.py` — `FakeLLMProvider` satisfies the ABC.
- `test_curator_review_similarity.py` — TF-IDF cosine truth table.
- `test_curator_review_report.py` — golden file.
- `test_curator_review_planner.py` — candidate filtering + cost-cap math + input_hash determinism.
- Extend `test_errors.py`, `test_models.py`, `test_config.py`, `test_never_delete_invariant.py`.

Run: `uv run pytest backend/tests/unit -v`.

### Integration Tests

In `backend/tests/integration/`. All carry `pytestmark = pytest.mark.integration` so they auto-skip when the emulator stack is down.

- `test_curator_review_end_to_end.py` — happy path.
- `test_curator_review_approve_patch.py`, `test_curator_review_approve_merge.py`, `test_curator_review_reject.py`, `test_curator_review_stale_etag.py` — apply / reject paths.
- `test_curator_review_lock_contention.py`.
- `test_curator_review_cost_cap.py`.

Run: `make up && uv run pytest backend/tests/integration -v -m integration -k curator_review`.

### Edge Cases

- Candidate has no SKILL.md in bundle → log warn, skip; not a proposal.
- LLM returns malformed JSON → log warn, drop proposal for that skill; do not raise.
- LLM returns `verdict="merge"` for a single skill → reject; not a valid output (consolidation pass requires `merged_skill_ids` length ≥ 2).
- `proposal.target_etags[skill_id]` missing the current skill (skill deleted? — should never happen given never-delete; defensive log + `ReviewProposalStale`).
- Two managers approve the same proposal simultaneously → second sees `status != "pending"` → 409.
- Foundry returns `usage=None` → record `input_tokens=0, output_tokens=0`; cost guard still works because zero tokens never breaches cap.
- Cost-cap breached on the very first call → 0 proposals emitted, record reflects abort.
- `curator_review_provider="fake"` in production env → `FakeLLMProvider(canned=[])` raises on first call; surface as `LLMProviderError`. Mitigate via startup check (Task 9 GOTCHA in the foundry provider can be paired with a Settings validator).

---

## VALIDATION COMMANDS

### Level 1: Syntax & Style

```bash
uv run ruff check .
uv run ruff format --check .
```

### Level 2: Unit Tests

```bash
uv run pytest backend/tests/unit -v
```

### Level 3: Integration Tests

```bash
make up
uv run pytest backend/tests/integration -v -m integration -k curator_review
uv run pytest backend/tests/integration -v -m integration  # full suite, M2 + M3
```

### Level 4: Manual Validation

```bash
# Bring up the stack (M2 components).
make up && make api &
make worker &

# Trigger an LLM review pass with the fake provider (set CURATOR_REVIEW_PROVIDER=fake
# and seed canned responses via a test-only env hook OR just use Foundry against a
# small deployment if available).
curl -X POST http://localhost:8000/v1/admin/curator/review \
  -H 'X-User-Email: admin@org'

# List pending proposals.
curl 'http://localhost:8000/v1/admin/curator/reviews?status=pending' \
  -H 'X-User-Email: admin@org'

# Inspect a single proposal.
curl 'http://localhost:8000/v1/admin/curator/reviews/{pid}?run_id={rid}' \
  -H 'X-User-Email: admin@org'

# Approve.
curl -X POST 'http://localhost:8000/v1/admin/curator/reviews/{pid}/approve?run_id={rid}' \
  -H 'X-User-Email: admin@org'

# Reject.
curl -X POST 'http://localhost:8000/v1/admin/curator/reviews/{pid}/reject?run_id={rid}&reason=irrelevant' \
  -H 'X-User-Email: admin@org'
```

### Level 5: Additional Validation (Optional)

```bash
# Determinism: re-running the review pass with the same fake provider + same skill
# input bytes must produce the same input_hash for each candidate.

# AST gate negative test: insert a `await skills.delete_item(...)` into
# backend/services/curator_review_apply.py, run:
uv run pytest backend/tests/unit/test_never_delete_invariant.py -v
# Expect: fail. Remove the insertion and re-run; expect: pass.
```

---

## ACCEPTANCE CRITERIA

- [ ] `POST /v1/admin/curator/review` triggers a review pass that respects pause + lock and writes a `CuratorReviewRunRecord` to Blob.
- [ ] Review pass selects at most `curator_review_max_skills_per_run` candidates, filtered by `status='approved' AND pinned=false AND uploader STARTSWITH @prefix`.
- [ ] Each candidate's SKILL.md is read from the published Blob bundle (not from the Cosmos `skill_md_text` cache).
- [ ] Drift verdicts (`patch` / `keep`) and consolidation verdicts (`merge`) are persisted as rows in `review_proposals` with `status="pending"` (or `"noop"` for `keep`).
- [ ] Each proposal records `target_etags`, `input_hash`, `usage` (input/output tokens, model_id, prompt_version).
- [ ] Cost-cap breach aborts the loop and sets `aborted_reason="cost_cap"`; no exception raised.
- [ ] `GET /v1/admin/curator/reviews` lists proposals; `GET /v1/admin/curator/reviews/{id}?run_id=` fetches one.
- [ ] `POST /v1/admin/curator/reviews/{id}/approve?run_id=` applies the proposal:
  - Patch: bundle rebuilt, version bumped, Cosmos updated, audit `patch_apply`, snapshot present, prior bundle bytes untouched.
  - Merge: umbrella skill created `pending` and enqueued for classifier; merged-in skills `archived` with bytes copied to `archive/` and source untouched; audit `merge_apply` per archived skill.
- [ ] `POST /v1/admin/curator/reviews/{id}/reject?run_id=` updates proposal `status="rejected"`; no skill or blob mutation.
- [ ] Stale-etag approval returns 409 `REVIEW_PROPOSAL_STALE`; proposal `status="stale"`.
- [ ] Concurrent review-pass + deterministic-pass: one wins the lock, the other reports `aborted_reason="lock"` or raises `LockUnavailable` (review pass returns the record; deterministic pass raises as today).
- [ ] AST gate at `backend/tests/unit/test_never_delete_invariant.py` includes the new review modules and passes.
- [ ] No new LLM provider other than Foundry + Fake exists in the repo.
- [ ] Per-run report written to `{curator_reports_container}/reviews/{run_id}/{run.json, REPORT.md}`.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` clean.
- [ ] Full integration suite passes against the local docker-compose stack with `CURATOR_REVIEW_PROVIDER=fake`.
- [ ] All M0/M1/M2 tests still pass; no regressions.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order.
- [ ] Each task validation passed immediately.
- [ ] All validation commands executed successfully.
- [ ] Full test suite passes (unit + integration).
- [ ] No linting or type checking errors.
- [ ] Manual validation walkthrough confirms approve + reject paths work end-to-end on the local stack.
- [ ] `AGENTS.md` §5 updated; "Key Files" table updated.
- [ ] Acceptance criteria all met.
- [ ] Code reviewed for adherence to the four Redis rules and the never-delete invariant.
- [ ] No OpenAI/Anthropic SDK imports anywhere in the repo (grep verified).

---

## NOTES

**Why proposals live in a separate Cosmos container (not on the skill doc).** Proposals are 1-to-many per skill (multiple runs can produce overlapping suggestions) and have their own lifecycle (`pending → approved → applied`). Folding them into `skills` would bloat hot reads + complicate every catalog query. `/run_id` PK makes per-run listing cheap and partition-scoped.

**Why `run_id` as PK and as a required query param on item endpoints.** Avoids cross-partition reads on the hot path. Trade-off: the URL is slightly uglier. Worth it.

**Why no auto-apply, even for `kind="keep"`.** `keep` proposals are noop verdicts — they record "the LLM looked at this and had no suggestions". They are not applied because there is nothing to apply; they go straight to `status="noop"`.

**Why merge produces a `pending` umbrella, not a directly-published one.** Merge is a significant catalog change. Forcing the umbrella through the standard upload → classify → manager-approve pipeline ensures (a) the classifier scores it, (b) a manager confirms it's a sensible skill, (c) audit trail matches the standard publish flow. Cost: two manager clicks instead of one. Acceptable.

**Why the AST gate over a runtime check.** Runtime checks cost CI minutes and can be bypassed in dev. The AST gate is millisecond-fast, catches the pattern even in code paths integration tests don't exercise, and produces a clear failure message pointing at the offending line.

**Why we don't snapshot at review-pass time.** The review pass produces no mutations. Snapshots are expensive (download every blob, build a tar, upload twice). Each apply call takes its own snapshot — cheaper and more meaningful (snapshot reflects state just before *this specific* mutation).

**Why `target_etag` instead of a content hash.** Cosmos `_etag` is monotonic per item and free to read. A content hash would re-hash the bundle on every apply call. `_etag` mismatch is a sufficient and minimal signal for "the doc changed; re-review".

**Why TF-IDF in stdlib instead of `scikit-learn`.** scikit-learn is a heavy dependency (numpy, scipy) that we don't otherwise need. The pre-filter is over ≤50 documents — pure-Python is fast enough. If we add sklearn for other reasons later, swap freely.

**Why Foundry-only.** Per direct user constraint. Multi-provider abstraction adds surface area we don't currently need; the `LLMProvider` ABC is still cheap insurance (test injection alone justifies it). Adding OpenAI/Anthropic later is a one-file addition + DI update; doing it speculatively now would violate YAGNI.

**Why the second cron job defaults disabled (`curator_review_enabled=False`).** Foundry deployment is environment-specific. Default-off lets local dev boot the worker without a Foundry endpoint. Enable explicitly per env.

**Confidence score for one-pass implementation: 7/10.** The review pass itself is bounded and well-typed. The two risks are (a) Foundry SDK parameter shape (the SDK is in beta; minor signature drift is plausible — Task 9 mitigates by wrapping in `LLMProviderError`) and (b) the merge-apply path is the most complex new code in M3 (umbrella creation + classifier enqueue + per-source archive). Tests at Task 24 are designed to surface drift on first run.
