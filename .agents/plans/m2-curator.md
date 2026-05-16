# Feature: M2 — Curator (Usage Pipeline + Lifecycle Maintenance + Snapshots + Rollback + Pinning)

The following plan should be complete, but it is important that you validate documentation, codebase patterns, and task sanity before you start implementing. M0 (end-to-end POC on emulators) and M1 (Azure deploy + Entra OIDC + API keys + App Insights) are already merged. M2 adds the lifecycle maintenance layer that keeps the published catalog clean over time, **without changing the M0/M1 spine** and **without ever silently destroying data**.

Pay special attention to:
- **Storage placement is non-negotiable** (AGENTS.md §3 + §4). Usage *state* (counters, last_loaded_at, transition status) lives in Cosmos. Redis only holds 60s catalog caches that must be invalidated on every state mutation; raw usage events go to the `usage_events` Cosmos container (TTL 90d, already provisioned in `backend/core/cosmos.py:55-60`). Blob holds snapshots and archived bundle bytes only.
- **The never-delete invariant** (AGENTS.md §5) is the single most important constraint in this milestone. The curator may transition status (`approved → active → stale → archived`), move bytes between Blob containers (`published/` → `archive/`), and write snapshots and reports — it must never call `delete_item` against `skills`, never call `delete_blob` against a `published/` or `archive/` blob, and never bypass a `pinned=True` flag. CI must enforce this with a grep-based static check (see Task 18).
- **Pinning is absolute.** A `pinned=True` skill is skipped by every transition rule, ignored by every consolidation suggestion, and refused by every admin "delete" code path (there is no admin delete in M2 anyway — only `archive` via a curator pass). Pinning state lives on the `SkillDoc` (`pinned`, `pinned_by`) — already on the model at `backend/models/skill.py:62-63`. We are not adding a new container.
- **Distributed lock prevents concurrent curator passes.** Reuse `redis_lock(...)` from `backend/core/redis.py:56-75`. Lock key: `lock:curator:run`. TTL: 30 minutes (configurable). If the lock cannot be acquired, the run returns `LockUnavailable` (HTTP 423) — never blocks waiting.
- **Dry-run and real-run must produce byte-identical reports** for the same input snapshot. This is asserted by an integration test (Task 31). The only difference between dry-run and real-run is whether mutations are applied; the *planner* is pure.
- **Rollback is itself reversible.** A `rollback` operation snapshots the *current* state before restoring the target snapshot, so an operator who rolls back the wrong snapshot can roll forward again. This is non-obvious; the rollback service writes a snapshot tagged `pre-rollback-{utc-iso}` before any restore writes happen.

## Feature Description

The Curator is a scheduled background process that maintains the published Skill Hub catalog over time. It does three things:

1. **Ingest usage telemetry.** Agent runtimes `POST /v1/skills/{id}/usage` whenever they load a skill. Each event lands as a raw row in the Cosmos `usage_events` container (90d TTL) and atomically increments aggregated counters on the corresponding `SkillDoc` (`usage.load_count`, `usage.last_loaded_at`, `usage.loaders_30d`). On every counter update the catalog list cache (`cache:skills:list:v1`) and the per-skill item cache (`cache:skills:item:{id}`) are invalidated.

2. **Run lifecycle transitions on a schedule.** A deterministic state machine evaluates each approved skill against its `usage.last_loaded_at` and transitions it: no loads in 30d → `stale`; no loads in 90d → `archived` (bytes moved from `published/{id}/{version}/bundle.tar.gz` to `archive/{id}/{version}/bundle.tar.gz`; status flipped in Cosmos; audit row written). Pinned skills are skipped. Every real pass is preceded by a full snapshot of the published Blob tree (`snapshots/{utc-iso}/skills.tar.gz` + a `manifest.json` listing every skill_id/version/status/checksum captured). Every pass writes a per-run report to `curator/runs/{utc-iso}/{run.json, REPORT.md}`.

3. **Provide operator controls.** Admins drive the curator via `/v1/admin/curator/*` endpoints: `pause`, `resume`, `run` (default real), `run?dry_run=true`, `rollback` (most recent snapshot), `rollback?id={snapshot-name}` (specific), `restore/{name}` (restore an archived skill back to approved), `pin/{name}` + `unpin/{name}` (pinning controls already implied by the model), `status` (current pause state + last run summary). Concurrency-safe via Redis `SET NX` distributed lock.

Two adjacent maintenance jobs ship in the same milestone because they share infrastructure:

- **Janitor sweep for the classifier queue** (rule-#4 mitigation from AGENTS.md §4): scans Cosmos for `classifier_status=queued` docs older than `(classifier_blpop_timeout_seconds * 5)` and re-pushes their `doc_id` onto `queue:classifier`. Run on the same scheduler as the curator, but as a separate command so admins can run them independently.

- **CLI surface** mirroring the admin endpoints, so operators can run rollback from a shell without going through HTTP: `python -m backend.workers.curator {run,dry-run,rollback,restore,status} [--id=...] [--name=...]`. This is the recommended path for production rollback because it doesn't require a healthy app tier.

## User Story

As a **platform admin** I want the catalog to auto-archive skills nobody has used in 90 days, with a snapshot taken before every change and a one-command rollback if a pass goes wrong, so the catalog stays clean without me ever risking data loss.

As a **manager** I want to pin a high-value skill so the curator never touches it regardless of usage signals, so a niche-but-critical skill isn't archived just because it's used rarely.

As an **agent runtime** I want to `POST /v1/skills/{id}/usage` on every skill load so the curator has real signal to work with, and I want this call to be cheap (one Cosmos write, no synchronous LLM, no Blob touch).

As a **future me debugging an over-eager pass** I want a per-run report at `curator/runs/{utc-iso}/REPORT.md` listing every transition the curator made or would make, with before/after status, so I can diff dry-run against real-run and prove they were equivalent.

## Problem Statement

After M1, the catalog grows monotonically. Every approved skill stays `approved` forever. There is no signal as to which skills are actually used by agent runtimes, no automatic pruning of dead entries, no way for an admin to know which skills are load-bearing vs. inert. Manager review is a one-time gate; nothing maintains quality over time. If we ship M2 wrong — by allowing the curator to silently delete a skill someone needed, or by losing usage history, or by letting two curator passes run concurrently and corrupt state — we permanently lose trust in the platform.

Three concrete failure modes M2 must prevent:

1. **Silent loss.** A bug, an over-eager rule change, or an operator pressing the wrong button must never result in a deleted skill. Worst case is archival, which is reversible.
2. **Pinned skills getting archived.** A skill pinned by an admin must survive every curator pass forever, regardless of usage. CI must verify pinning logic is applied at the planner level, not optionally at the executor level.
3. **Lost classifier messages.** Rule #4 explicitly cites this as the one place Redis holds in-flight data. M2 finally ships the janitor sweep that closes the loop.

## Solution Statement

1. **Usage pipeline (Phase 2.A).** New endpoint `POST /v1/skills/{id}/usage` (in `backend/api/skills.py`, replacing the M0 `NotImplementedM0` stub at line 88-90) accepts a `UsageEvent` body, validates `Principal` is either a `User` or a `ServiceAccount`, writes a row to `usage_events` (PK `/skill_id`, TTL 90d, no manual TTL — already on the container at `backend/core/cosmos.py:55-60`), then atomically increments counters on the skill doc using Cosmos optimistic concurrency (read with `_etag`, replace with `if_match=etag`, retry up to 3x on 412), then invalidates `cache:skills:list:v1` and `cache:skills:item:{id}`. `loaders_30d` is recomputed cheaply by querying `usage_events` for distinct loader_ids in the last 30d (small query, partitioned by skill_id). Failure to write the raw event is fatal (return 503); failure to invalidate cache is non-fatal (log warning, return 202).

2. **State machine (Phase 2.B).** A pure planner function `plan_transitions(skills_snapshot, now, *, pinned_ids, stale_days=30, archive_days=90) -> list[Transition]` in `backend/services/curator.py` walks every doc with `status in {"approved","active","stale"}` and emits transitions:
   - `last_loaded_at is None or last_loaded_at >= now - 30d` → no change (status `approved`/`active` is steady-state).
   - `last_loaded_at < now - 30d and last_loaded_at >= now - 90d` → transition to `stale`.
   - `last_loaded_at < now - 90d` → transition to `archived`.
   - `pinned == True` → never emit a transition. Always log a `Skipped` reason in the run report.
   Notes: `active` is a logical synonym for `approved` once usage exists; the planner treats them identically. The status enum already permits both via `SkillStatus` (`backend/models/skill.py:10`). The planner is deterministic and side-effect-free — same input snapshot + same `now` produces the same output, which is what makes dry-run and real-run byte-identical.

3. **Snapshot-before-pass (Phase 2.C).** Before any real-run executes a transition, `snapshot_published(blob, settings, *, run_id)` (in `backend/services/snapshot.py`) iterates the `published/` container, builds a deterministic tar.gz of every blob (sorted by name, mtime=0, mode 0o644 — same trick as `build_tar` at `backend/services/skill_bundle.py:63-86`), uploads it to `snapshots/{utc-iso}/skills.tar.gz`, and writes a sibling `snapshots/{utc-iso}/manifest.json` containing `{run_id, captured_at, skills:[{skill_id, version, status, checksum_sha256, blob_path}]}`. Snapshot retention: keep the most recent N (default 5) snapshot prefixes; older ones are *moved* (not deleted — moved to `snapshots/_retired/{utc-iso}/`) so a deep restore is still possible until an operator manually purges with a separate, audited admin command (out of scope for M2).

4. **Rollback (Phase 2.D).** `rollback(blob, cosmos, *, snapshot_name=None) -> RollbackResult` in `backend/services/curator_rollback.py`:
   1. Acquires the curator lock (`lock:curator:run`) — refuses to run if a curator pass is in flight.
   2. Resolves `snapshot_name` (latest if None) and downloads its `manifest.json`.
   3. Calls `snapshot_published(...)` with prefix `snapshots/pre-rollback-{utc-iso}/` *before* any restore writes — this makes rollback reversible.
   4. For each entry in the manifest: re-uploads the bytes to `published/{skill_id}/{version}/bundle.tar.gz` (overwrite=True), then replaces the Cosmos doc with the snapshotted `status`/`bundle` fields. Cosmos write happens *after* Blob write (rule: Cosmos is SoR but for rollback we need bytes back in place first so a partial failure leaves Cosmos pointing at present bytes, not missing ones — this inversion is documented in the file's module docstring).
   5. Writes one `audit` row per restored skill (`action="rollback"`, `before`/`after` showing the swap).
   6. Invalidates `cache:skills:list:v1` and every `cache:skills:item:{id}` touched.
   7. Returns a `RollbackResult` summary; also writes a `curator/runs/rollback-{utc-iso}/REPORT.md` for symmetry with normal runs.
   Byte-for-byte equality: an integration test (Task 32) computes `sha256` of every published blob before snapshot → curator real-run → rollback, and asserts the final set equals the original set.

5. **Pinning (Phase 2.E).** Already modeled at `backend/models/skill.py:62-63` (`pinned: bool`, `pinned_by: str | None`). M2 ships:
   - Two admin endpoints `POST /v1/admin/curator/pin/{skill_id}` and `POST /v1/admin/curator/unpin/{skill_id}` (under the curator router so admins find every lifecycle control in one place).
   - The planner reads `pinned` and skips. There is no separate "pin list" data structure — the single source of truth is the skill doc.
   - There is no admin-delete code path being introduced in M2; explicit grep test (Task 18) verifies no `delete_item` against `skills` is added anywhere in the new code.

6. **Admin endpoints (Phase 3).** New router `backend/api/curator.py` mounted under `/v1/admin/curator`:
   - `POST /pause` → sets a Redis flag `curator:paused` (string `"1"`, TTL=0 = persistent — this is the one exception to rule #3 because operator intent must persist across Redis restarts; mitigated by Cosmos-persisted shadow doc — see below).
   - `POST /resume` → deletes the flag, writes audit row, removes shadow doc.
   - `POST /run` (query: `dry_run: bool = False`) → invokes curator service; honors pause flag; respects lock.
   - `POST /rollback` (query: `id: str | None = None`) → invokes rollback service; rejects if curator is currently running.
   - `POST /restore/{skill_id}` → restores a single archived skill: Blob copy `archive/` → `published/`, status flip `archived` → `approved`, audit row, cache invalidate.
   - `POST /pin/{skill_id}` and `POST /unpin/{skill_id}`.
   - `GET /status` → returns `{paused: bool, lock_held: bool, last_run: {...}, schedule_next: iso8601, schedule_enabled: bool}`.
   - **Pause-flag durability fix:** to keep rule #3 honest, the `pause` endpoint writes a `curator_state` row to the `skills` container with `id="_curator_state"` and `skill_id="_curator_state"` (a reserved partition; doc shape distinguishable from a real skill via a marker field). The Redis flag is the hot-path read; on Redis miss, the curator falls back to Cosmos (rule #2). This way the only Redis-persistent key is a cache of a Cosmos-persisted truth.

7. **Janitor sweep (Phase 4).** `janitor_classifier_queue(skills, redis, settings, *, now)` in `backend/services/janitor.py`:
   - Query: `SELECT * FROM c WHERE c.classifier_status='queued' AND c.uploaded_at < @cutoff`.
   - `cutoff = now - (classifier_blpop_timeout_seconds * 5)`.
   - For each result: `redis.rpush(key_queue_classifier(), doc_id)` (right-push so re-queued jobs go to the back, not jumping ahead of fresh uploads). Write audit row `action="classify"` with `metadata={"requeued_by":"janitor"}` so the audit trail captures the re-queue.
   - Mounted on the same scheduler as the curator pass; also exposed as `python -m backend.workers.janitor` for ad-hoc runs and as a `POST /v1/admin/curator/janitor` admin endpoint (small surface, same router).

8. **Per-run report (Phase 5).** Every curator pass — dry-run or real — writes two Blob objects under `curator/runs/{utc-iso}/`:
   - `run.json` — machine-readable: `{run_id, started_at, finished_at, dry_run, planner_inputs:{stale_days,archive_days,now}, transitions:[{skill_id, version, before, after, reason, applied}], skipped_pinned:[skill_id...], snapshot_name, lock_token}`.
   - `REPORT.md` — human-readable rendering of the same data, with a summary table. Generated by a pure function `render_report(run_record: CuratorRunRecord) -> str` so it's unit-testable without Blob.
   `applied=False` in dry-run, `applied=True` in real-run. The two files for a dry-run + real-run pair must be identical in the `transitions` set ordering (sorted by `skill_id`) — that's the dry-run/real-run equivalence guarantee that Task 31 asserts.

## Feature Metadata

**Feature Type**: New Capability (usage pipeline + scheduled curator + admin lifecycle endpoints + janitor) — layered on the existing M0/M1 spine, no breaking changes.
**Estimated Complexity**: High (deterministic planner, snapshot/rollback round-trip, distributed lock semantics, never-delete invariant verified by both tests and a CI static check, scheduler wiring).
**Primary Systems Affected**: `backend/api/skills.py` (usage endpoint), new `backend/api/curator.py`, new `backend/services/{curator,curator_rollback,snapshot,janitor,usage}.py`, new `backend/workers/curator.py` (CLI + scheduler), `backend/core/redis.py` (new key helpers + lock key), `backend/core/blob.py` (new helpers for snapshots/archive moves), `backend/models/{api,curator}.py` (new request/response DTOs + run record).
**Dependencies**:
- Python adds: `apscheduler>=3.10` (lightweight in-process scheduler for the worker process; in prod the same code is invoked by Azure Functions timer trigger — APScheduler is the local-dev path only, keeping rule "local-first" from AGENTS.md §6 intact). No LLM dependency in M2 — the LLM review pass is M3.
- No new infra resources. Cosmos `usage_events` container is already created with TTL at `backend/core/cosmos.py:55-60`. Blob `snapshots` and `archive` containers are already created at `backend/core/blob.py:25-37`.
- Frontend additions are out of scope for M2 (no UI work). Admin endpoints will be driven by `curl`/CLI; M3+ adds a UI tab.

---

## CONTEXT REFERENCES

### Relevant Codebase Files IMPORTANT: YOU MUST READ THESE FILES BEFORE IMPLEMENTING!

- `AGENTS.md` (entire file) — Especially §3 (storage split), §4 (four Redis rules), §5 (never-delete invariant), §8 (DI patterns). M2 is the milestone where every one of these rules gets tested by hostile cases. Re-read §5 before writing any code that touches `skills` or Blob.
- `docs/PRD.md` lines 529–540 — M2 deliverables checklist. Validation: "Dry-run report matches real-run diff; rollback restores prior state byte-for-byte; pinned skills survive a full curator cycle untouched."
- `docs/PRD.md` lines 257–267 — Curator hard invariants (never auto-deletes, pinned immune, snapshot before pass, dry-run produces report with no mutations).
- `.agents/plans/m0-poc-end-to-end-skill-submission.md` — M0 patterns the planner must mirror (Cosmos-first writes, audit on every transition, Redis fallback).
- `.agents/plans/m1-azure-deployment-and-auth.md` lines 36–47 — `Principal` union + `IdentityProvider`. The usage endpoint and admin curator endpoints accept `Depends(get_principal)` (catalog/usage) and `Depends(require_role("admin"))` (curator controls), following the same DI patterns introduced in M1.
- `backend/services/publish.py` (entire file, especially lines 36–111) — Canonical example of the Cosmos-first + audit + Redis-invalidate-last sequence. Curator transitions follow the *same* ordering for each transition. The module docstring at lines 1–13 is the template for the curator service docstring.
- `backend/services/catalog.py` (lines 38–66, 69–100) — Cache-on-read + Cosmos fallback pattern. Usage counter writes invalidate the same two cache keys this file populates (`cache:skills:list:v1`, `cache:skills:item:{id}`).
- `backend/services/audit.py` (lines 16–36) — Audit write API. Every curator transition MUST call this; the curator integration test asserts row counts.
- `backend/services/skill_bundle.py` (lines 63–86) — Deterministic tar building. The snapshot service borrows this exact mtime=0/sorted-entries pattern so snapshots are byte-stable for the same input.
- `backend/core/cosmos.py` (lines 17–66) — Container names + `usage_events` already exists with `default_ttl=90*86400`. Do not create a new container; query the existing one. Note also `API_KEYS_CONTAINER` was added in M1 — the curator does not need a new container.
- `backend/core/blob.py` (lines 25–98) — `ensure_containers` already creates `snapshots` and `archive`. `published_blob_path` is the canonical naming helper. `signed_download_url` is the read pattern. The snapshot/restore code reuses these.
- `backend/core/redis.py` (lines 37–75) — Key helper conventions and `redis_lock` context manager. M2 adds: `key_curator_pause()`, `key_curator_run_lock()`, `key_cache_item(skill_id)` (already exists). Do not invent a parallel locking primitive.
- `backend/core/config.py` (lines 22–131) — Settings pattern. M2 adds: `curator_stale_days: int = 30`, `curator_archive_days: int = 90`, `curator_lock_ttl_seconds: int = 1800`, `curator_snapshot_retention: int = 5`, `curator_schedule_cron: str = "0 3 * * *"`, `usage_loaders_30d_window_days: int = 30`, `janitor_classifier_stale_multiplier: int = 5`. All have defaults so local boot still works zero-config.
- `backend/core/deps.py` (lines 24–54) — DI factories. M2 adds: `get_usage_container` is already present at line 45. Add `get_curator_state_service` if needed (or just inject the three primitives — prefer the latter to match `publish.py`).
- `backend/core/errors.py` (lines 13–86) — DomainError pattern. M2 adds: `CuratorPaused` (409), `CuratorLocked` (423 — reuse `LockUnavailable` if semantically equivalent), `SnapshotNotFound` (404), `RestoreFailed` (500 — bytes back, status flip failed, etc.). Use the existing pattern; do not invent a new base class.
- `backend/api/admin.py` (lines 31–98) — Router + `require_role("admin")` pattern. The new `backend/api/curator.py` mirrors this exact shape. After M1's role collapse to `user`/`admin`, the gate is `require_role("admin")`.
- `backend/api/skills.py` (lines 88–90) — The M0 `NotImplementedM0("usage ingestion will land in M2")` stub. This is the call site replaced by the real usage endpoint.
- `backend/workers/classifier.py` (entire file) — Worker process pattern: signal handling, lifespan, `asyncio.Event`-based shutdown, `configure_telemetry`, single `main()`. The curator worker mirrors this exactly.
- `backend/tests/integration/test_e2e_happy_path.py` — End-to-end test pattern (ASGI client + lifespan + `_cleanup`). The curator integration tests follow the same shape.
- `backend/tests/integration/test_redis_lock.py` — Lock + AOF assertion patterns. The curator lock test uses the same `LockUnavailable` raise + `redis_lock` ctx.
- `backend/models/skill.py` (lines 10, 35–40, 62–63) — `SkillStatus`, `UsageCounters`, `pinned`, `pinned_by`. No model changes needed beyond M0; the counters and pinning fields are already present.
- `backend/models/audit.py` (lines 11–26) — `AuditAction` already includes `archive`, `pin`, `unpin`, `restore`, `rollback`. No additions needed for M2.

### New Files to Create

- `backend/api/curator.py` — `/v1/admin/curator/*` router (pause/resume/run/rollback/restore/pin/unpin/status/janitor).
- `backend/services/curator.py` — Planner (`plan_transitions`) + executor (`execute_pass`) + scheduling helpers.
- `backend/services/curator_rollback.py` — Rollback service (snapshot-before-restore, byte-for-byte restore, audit).
- `backend/services/snapshot.py` — `snapshot_published`, `manifest_for`, `download_snapshot`, retention rotation (to `_retired/`).
- `backend/services/janitor.py` — Classifier-queue re-queue sweep.
- `backend/services/usage.py` — `record_usage_event` (raw event + counter increment + cache invalidate); `recompute_loaders_30d` helper.
- `backend/services/curator_state.py` — Pause-flag service (Cosmos-persisted truth + Redis hot-cache).
- `backend/services/curator_report.py` — `render_report(run_record) -> str` pure function for `REPORT.md` rendering.
- `backend/workers/curator.py` — CLI + APScheduler wrapper around the curator service; `python -m backend.workers.curator {run,dry-run,rollback,restore,status,schedule}`.
- `backend/workers/janitor.py` — Thin CLI wrapper around `services/janitor.py`.
- `backend/models/curator.py` — `UsageEvent` (request DTO), `UsageEventDoc` (Cosmos doc), `Transition`, `CuratorRunRecord`, `RollbackResult`, `SnapshotManifest`, `SnapshotManifestEntry`, `CuratorStatus`.
- `backend/tests/unit/test_curator_planner.py` — Planner truth tables (steady-state, stale, archived, pinned-skipped, edge cases).
- `backend/tests/unit/test_curator_report.py` — `render_report` golden-file tests.
- `backend/tests/unit/test_snapshot_deterministic.py` — Same input → same tar bytes.
- `backend/tests/unit/test_usage_counter_math.py` — `loaders_30d` recompute on a synthetic event window.
- `backend/tests/integration/test_curator_dry_vs_real.py` — Dry-run + real-run on identical fixture; `transitions` set byte-equal.
- `backend/tests/integration/test_curator_rollback_round_trip.py` — Publish → snapshot → archive → rollback → assert blob sha256 set equals original.
- `backend/tests/integration/test_curator_pinned_immune.py` — Pinned skill survives a forced 91-day-stale curator pass.
- `backend/tests/integration/test_curator_lock_contention.py` — Two concurrent `execute_pass` calls — second raises `LockUnavailable`.
- `backend/tests/integration/test_curator_pause_durability.py` — Pause → flush Redis → call `run` → still paused (Cosmos fallback).
- `backend/tests/integration/test_usage_endpoint.py` — POST usage → event in Cosmos, counters bumped, caches invalidated.
- `backend/tests/integration/test_janitor_requeue.py` — Insert old queued doc → janitor → doc id is on `queue:classifier`.

### Relevant Documentation YOU SHOULD READ THESE BEFORE IMPLEMENTING!

- [Cosmos optimistic concurrency](https://learn.microsoft.com/azure/cosmos-db/nosql/database-transactions-optimistic-concurrency#implementing-optimistic-concurrency-control) — Why: Counter increment uses `if_match=etag` to avoid lost updates from concurrent agent runtime POSTs.
- [Cosmos default TTL](https://learn.microsoft.com/azure/cosmos-db/nosql/time-to-live#enable-time-to-live-on-a-container-using-the-azure-sdk) — Why: `usage_events` already has `default_ttl=90*86400`; raw events expire automatically — no manual cleanup.
- [Azure Blob copy operations](https://learn.microsoft.com/azure/storage/blobs/storage-blob-copy-async-rest-api) — Why: Archive move and restore are copy-then-(intentionally-do-not-)delete. We *intentionally leave the source* in `published/` for the lifetime of the run, then mark the Cosmos doc archived; a follow-up garbage sweep (post-M2) eventually moves the source. M2 takes the simpler path: copy bytes to `archive/` and update Cosmos; do not delete from `published/` (defense-in-depth; archived skills will fall out of catalog queries which filter `status='approved'`).
- [APScheduler AsyncIOScheduler](https://apscheduler.readthedocs.io/en/3.x/userguide.html#starting-the-scheduler) — Why: In-process scheduler for local dev; same job invocation works under Azure Functions timer trigger in prod.
- [Redis SET NX EX](https://redis.io/commands/set/#options) — Why: Already used by `redis_lock`; M2 reuses, does not reimplement.
- AGENTS.md §5 — Why: The single most important section for this milestone. Read it before writing any code that touches Blob or Cosmos `skills`.

### Patterns to Follow

**Module docstring (canonical example: `backend/services/publish.py:1-13`).** Every new service module begins with a docstring spelling out the Cosmos/audit/Redis ordering for its operations and citing the relevant AGENTS.md rule by section number. Reviewers grep for this.

**Cosmos-first ordering inside each transition (mirrors `publish.py:89-110`):**

```python
# 1. Blob mutation (snapshot → archive copy / published overwrite for rollback).
# 2. Cosmos write — SOURCE OF TRUTH FLIP — via replace_item with if_match=etag.
# 3. Audit write (immutable row).
# 4. Redis invalidation — LAST, failures non-fatal.
```

For the curator, "Blob mutation" specifically means "copy bundle to archive prefix" (archive transition) or "no-op" (stale transition, which has no bytes movement, only a Cosmos status flip). Rollback inverts step 1+2 (Blob first, then Cosmos) — documented in the module docstring with a rationale paragraph.

**Naming Conventions:**
- Snake_case for Python; modules named for the noun (`curator.py`, `snapshot.py`, `usage.py`) not the action.
- Redis keys: `domain:subject:purpose[:specifier]`. Add to `backend/core/redis.py`:
  - `key_cache_list()` (exists, line 37)
  - `key_cache_item(skill_id)` (exists, line 41)
  - `key_queue_classifier()` (exists, line 45)
  - `key_lock_publish(skill_id)` (exists, line 49)
  - **New:** `key_curator_run_lock() -> "lock:curator:run"`
  - **New:** `key_curator_pause() -> "curator:paused"`

**Error Handling (mirrors `backend/core/errors.py:13-22`):** Subclass `DomainError`, set `error_code` and `http_status`. Reuse `LockUnavailable` for curator lock contention. Reuse `SkillNotFound` for restore-on-missing-skill. New: `CuratorPaused`, `SnapshotNotFound`, `RestoreFailed`.

**Logging Pattern (mirrors `backend/workers/classifier.py:51` and `publish.py:47`):** Call `bind(skill_id=..., actor=...)` at the top of any service function so JSON logs carry consistent fields. The curator binds `actor="system:curator"` and `run_id=...`. Logger comes from `backend.core.logging.get_logger(__name__)`.

**DI pattern (mirrors `backend/api/admin.py:42-62`):** Route handlers receive injected `ContainerProxy`/`BlobServiceClient`/`Redis` from `Depends(...)`; pass them through to service functions as keyword args. Services never call `get_settings()` or build clients themselves.

**Worker pattern (mirrors `backend/workers/classifier.py:102-168`):** Signal handlers set an `asyncio.Event`; main loop checks it between ticks; teardown is in `finally`. APScheduler runs as a child of the same loop.

**Test pattern (mirrors `backend/tests/integration/test_e2e_happy_path.py:50-83`):** ASGI client via `httpx.AsyncClient(transport=ASGITransport(app))` inside `app.router.lifespan_context(app)`; `_cleanup` is the first and last line of every test; integration tests carry `pytestmark = pytest.mark.integration` so they auto-skip when the emulator stack is down (see `conftest.py:31-42`).

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation (models, config, errors, key helpers, retention helpers)

Build the type system + plumbing first so subsequent phases compose with no churn.

**Tasks:**

- Add curator settings to `Settings`.
- Add domain errors (`CuratorPaused`, `SnapshotNotFound`, `RestoreFailed`).
- Add Redis key helpers (`key_curator_run_lock`, `key_curator_pause`).
- Create `backend/models/curator.py` with all new DTOs.
- Add `_etag`-aware skill-doc update helper in `backend/services/cosmos_helpers.py` (new) — wraps `replace_item(..., if_match=etag)` with up-to-3-retries on 412.

### Phase 2: Core Implementation (usage pipeline + planner + snapshot + executor + rollback)

**Tasks:**

- 2.A Usage service + endpoint.
- 2.B Curator planner (pure function).
- 2.C Snapshot service (publish → tar → blob + manifest, retention rotation to `_retired/`).
- 2.D Curator executor: lock acquisition → snapshot (if real) → apply transitions in Cosmos-first order → audit → cache invalidate → write report.
- 2.E Rollback service: lock → pre-rollback snapshot → restore bytes → restore Cosmos → audit → cache invalidate → report.
- 2.F Pause/resume + Cosmos-shadow pause state.
- 2.G Janitor service.
- 2.H Report rendering (pure).

### Phase 3: Integration (admin router, scheduler, worker, CLI)

**Tasks:**

- Create `backend/api/curator.py` with all admin endpoints.
- Mount in `backend/app.py:create_app` alongside the existing `admin_router`.
- Replace the `NotImplementedM0` stub in `backend/api/skills.py:88-90` with the real usage endpoint.
- Create `backend/workers/curator.py` (APScheduler + CLI).
- Create `backend/workers/janitor.py` (CLI wrapper).
- Add Make targets: `make curator`, `make janitor`, `make curator-dry-run`, `make curator-rollback`.

### Phase 4: Testing & Validation

**Tasks:**

- Unit tests: planner truth table, report rendering, deterministic snapshot, usage counter math.
- Integration tests: dry-run vs real-run equality, rollback round-trip, pinned-immune, lock contention, pause durability, usage endpoint, janitor requeue.
- E2E extension: append a "curator pass after fake aging" block to a new `test_e2e_curator.py` (mirror `test_e2e_happy_path.py` setup).
- Add CI grep gate: a `pytest` test that asserts no `delete_item` is called against the `skills` container anywhere in `backend/` (Task 18).

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### Task Format Guidelines

Use information-dense keywords for clarity:
- **CREATE**: New files or components
- **UPDATE**: Modify existing files
- **ADD**: Insert new functionality into existing code
- **REMOVE**: Delete deprecated code
- **REFACTOR**: Restructure without changing behavior
- **MIRROR**: Copy pattern from elsewhere in codebase

---

### Task 1: UPDATE `backend/core/config.py`

- **IMPLEMENT**: Add fields `curator_stale_days: int = 30`, `curator_archive_days: int = 90`, `curator_lock_ttl_seconds: int = 1800`, `curator_snapshot_retention: int = 5`, `curator_schedule_cron: str = "0 3 * * *"`, `usage_loaders_30d_window_days: int = 30`, `janitor_classifier_stale_multiplier: int = 5`, `curator_runs_container_prefix: str = "curator/runs"`, `curator_snapshots_retired_prefix: str = "snapshots/_retired"`.
- **PATTERN**: Mirror existing fields at `backend/core/config.py:85-88`.
- **GOTCHA**: Keep defaults non-empty so `Settings()` boots zero-config in tests.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_config.py -v`

### Task 2: UPDATE `backend/core/errors.py`

- **IMPLEMENT**: Add `CuratorPaused` (409 CONFLICT, `error_code="CURATOR_PAUSED"`), `SnapshotNotFound` (404, `"SNAPSHOT_NOT_FOUND"`), `RestoreFailed` (500, `"RESTORE_FAILED"`). Do NOT add a new lock error — reuse `LockUnavailable`.
- **PATTERN**: Mirror `backend/core/errors.py:25-77`.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_errors.py -v` (extend with assertions for the three new codes).

### Task 3: UPDATE `backend/core/redis.py`

- **ADD**: `def key_curator_run_lock() -> str: return "lock:curator:run"` and `def key_curator_pause() -> str: return "curator:paused"`. Place after `key_lock_publish` at line 50.
- **PATTERN**: Mirror `backend/core/redis.py:37-50`.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_redis_cache_keys.py -v` (extend tests).

### Task 4: CREATE `backend/models/curator.py`

- **IMPLEMENT**: Pydantic models:
  - `UsageEvent` (request body): `loader_id: str` (required), `context: dict[str, Any] = {}`.
  - `UsageEventDoc` (Cosmos doc): `id: str = uuid4().hex`, `skill_id: str`, `version: str`, `loader_id: str`, `at: datetime = utc_now`, `context: dict[str, Any]`. PK `/skill_id`.
  - `TransitionReason = Literal["steady_state","stale_30d","archive_90d","pinned","missing_usage_data"]`.
  - `Transition`: `skill_id: str`, `version: str`, `before: SkillStatus`, `after: SkillStatus`, `reason: TransitionReason`, `applied: bool = False`.
  - `SnapshotManifestEntry`: `skill_id, version, status, checksum_sha256, blob_path`.
  - `SnapshotManifest`: `run_id, captured_at, skills: list[SnapshotManifestEntry]`.
  - `CuratorRunRecord`: `run_id, started_at, finished_at, dry_run: bool, planner_inputs: dict, transitions: list[Transition], skipped_pinned: list[str], snapshot_name: str | None, lock_token: str | None`.
  - `RollbackResult`: `snapshot_name, pre_rollback_snapshot_name, restored: list[Transition], at: datetime`.
  - `CuratorStatus`: `paused: bool, lock_held: bool, last_run: CuratorRunRecord | None, schedule_enabled: bool, schedule_next: datetime | None`.
- **PATTERN**: Mirror `backend/models/skill.py:18-70` (Pydantic + utc-default factory + Literal-typed enums).
- **VALIDATE**: `uv run pytest backend/tests/unit/test_models.py -v` after extending it with one shape-roundtrip test per new model.

### Task 5: CREATE `backend/services/cosmos_helpers.py`

- **IMPLEMENT**: `async def replace_with_etag_retry(container, *, item_id: str, body: dict, partition_key: str, max_retries: int = 3) -> dict`. Reads, takes `_etag`, attempts `replace_item(item=item_id, body=body, etag=etag, match_condition=MatchConditions.IfNotModified)`; on `CosmosAccessConditionFailedError` (HTTP 412), re-reads and retries.
- **IMPORTS**: `from azure.cosmos import MatchConditions`; `from azure.core import MatchConditions` (verify SDK location at impl time).
- **GOTCHA**: The body passed to `replace_item` must NOT include the stale `_etag` — pop it. Tests must cover both "no contention" and "two concurrent writers, second retries".
- **VALIDATE**: `uv run pytest backend/tests/integration/test_cosmos_etag_retry.py -v` (create this file).

### Task 6: CREATE `backend/services/usage.py`

- **IMPLEMENT**:
  - `async def record_usage_event(*, skill_id: str, version: str, loader_id: str, context: dict, skills, usage, redis, settings) -> UsageEventDoc`:
    1. Build `UsageEventDoc`, `await usage.create_item(body=doc.model_dump(mode="json"))` (raw event first — TTL handles eviction).
    2. Read `SkillDoc` via `_load_latest` (mirror `publish.py:144-154` — copy the helper into this module or import).
    3. Bump counters: `usage.load_count += 1`, `usage.last_loaded_at = now`. Then `loaders_30d = await recompute_loaders_30d(usage, skill_id, now, window_days=settings.usage_loaders_30d_window_days)`.
    4. `replace_with_etag_retry(skills, ...)`.
    5. `await redis.delete(key_cache_list(), key_cache_item(skill_id))` (best-effort; non-fatal).
  - `async def recompute_loaders_30d(usage, skill_id, now, window_days) -> int`: `SELECT VALUE COUNT(DISTINCT c.loader_id) FROM c WHERE c.skill_id=@id AND c.at >= @cutoff`. Partitioned by `skill_id`.
- **PATTERN**: Cosmos-first + cache-invalidate-last mirrors `backend/services/publish.py:89-110`.
- **GOTCHA**: If the skill is `archived`, we still accept usage events (raw rows) but do NOT bump counters or invalidate cache (archived skills aren't in the list cache anyway). Document this in the module docstring.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_usage_endpoint.py -v`

### Task 7: UPDATE `backend/api/skills.py`

- **IMPLEMENT**: Replace lines 88-90 with a real handler:

  ```python
  @router.post("/{skill_id}/usage", status_code=202)
  async def report_usage(
      skill_id: str,
      body: UsageEvent,
      principal: Principal = Depends(get_principal),
      settings: Settings = Depends(settings_dep),
      skills: ContainerProxy = Depends(get_skills_container),
      usage: ContainerProxy = Depends(get_usage_container),
      redis: Redis = Depends(get_redis_client),
  ) -> dict:
      doc = await catalog_svc.get_skill(skill_id=skill_id, skills=skills, redis=redis, settings=settings)
      if doc is None:
          raise SkillNotFound(f"skill {skill_id!r} not found")
      await usage_svc.record_usage_event(
          skill_id=skill_id, version=doc.version,
          loader_id=body.loader_id, context=body.context,
          skills=skills, usage=usage, redis=redis, settings=settings,
      )
      return {"accepted": True}
  ```

- **PATTERN**: Mirror existing handler at `backend/api/skills.py:63-80`.
- **IMPORTS**: `from backend.services import usage as usage_svc`; `from backend.models.curator import UsageEvent`.
- **GOTCHA**: 202 not 201 — usage ingest is fire-and-forget semantically.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_usage_endpoint.py -v`

### Task 8: CREATE `backend/services/snapshot.py`

- **IMPLEMENT**:
  - `async def snapshot_published(blob, settings, *, run_id: str, prefix: str = "snapshots") -> SnapshotManifest`: iterate `blob.get_container_client(settings.blob_snapshots_container)` *no* — iterate `published_container.list_blobs()`. For each blob: download bytes, compute sha256, record `(skill_id, version, blob_path, checksum)`. Build a deterministic tar of `{blob_path: bytes}` using the same algorithm as `backend/services/skill_bundle.py:63-86`. Upload to `{prefix}/{utc-iso}/skills.tar.gz`. Build `SnapshotManifest`, upload as `{prefix}/{utc-iso}/manifest.json`. Return the manifest.
  - `async def list_snapshots(blob, settings) -> list[str]`: return snapshot folder names sorted desc.
  - `async def load_manifest(blob, settings, name: str) -> SnapshotManifest`: download `{name}/manifest.json`.
  - `async def download_snapshot_tar(blob, settings, name: str) -> bytes`: download `{name}/skills.tar.gz`.
  - `async def rotate_retention(blob, settings) -> list[str]`: keep newest `settings.curator_snapshot_retention`; **move** older to `snapshots/_retired/{name}/` (copy then leave source — see "Azure Blob copy" doc above). Never delete.
- **PATTERN**: Deterministic tar mirrors `backend/services/skill_bundle.py:63-86` exactly (same mtime=0, sorted, mode 0o644).
- **GOTCHA**: The snapshot must include archived bundles too if the operator wants symmetric rollback — but archived bytes live in `archive/`, not `published/`. M2 decision: snapshot covers `published/` only (the catalog surface). Archive restoration is a separate `POST /restore/{id}` flow that copies one bundle back.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_snapshot_deterministic.py -v && uv run pytest backend/tests/integration/test_snapshot_round_trip.py -v`

### Task 9: CREATE `backend/services/curator.py`

- **IMPLEMENT**:
  - Module docstring spelling out the Cosmos-first/audit/cache ordering (mirror `backend/services/publish.py:1-13`).
  - `def plan_transitions(docs: list[SkillDoc], now: datetime, *, stale_days: int, archive_days: int) -> tuple[list[Transition], list[str]]`: pure function. Returns `(transitions, skipped_pinned_ids)`. For each doc with `status in {"approved","stale"}`: if `pinned`, add to skipped. Else compute age from `usage.last_loaded_at` (treat None as infinitely old → eligible for archive only if `uploaded_at < now - archive_days`; if `uploaded_at` is within `stale_days`, leave as `approved` — newly published skills get a grace period equal to `stale_days`). Emit `Transition(applied=False)`.
  - `async def execute_pass(*, dry_run: bool, skills, audit, blob, redis, settings, now=None, actor="system:curator") -> CuratorRunRecord`:
    1. Check pause flag (`curator_state.is_paused(...)`); raise `CuratorPaused` if set.
    2. Acquire `redis_lock(redis, key_curator_run_lock(), ttl=settings.curator_lock_ttl_seconds)` — raises `LockUnavailable` on contention.
    3. `run_id = utc_iso_compact()`; `started_at = now`.
    4. Snapshot if not dry-run: `snapshot = await snapshot_published(blob, settings, run_id=run_id)`. Dry-run: `snapshot = None`.
    5. Load all candidate docs: `SELECT * FROM c WHERE c.status IN ('approved','stale')` (cross-partition).
    6. `transitions, skipped = plan_transitions(docs, now, ...)`.
    7. If real: for each transition (sorted by skill_id for determinism), in Cosmos-first order:
        - If `after=="archived"`: copy bytes `published/{id}/{ver}/bundle.tar.gz` → `archive/{id}/{ver}/bundle.tar.gz` (do NOT delete source — defense-in-depth).
        - `replace_with_etag_retry(skills, ...)` flipping status.
        - `audit_svc.record(audit, skill_id=..., action=("archive" if archived else "classify_failed-N/A — use generic 'archive' for both? NO: for stale we need a status-change audit action; M2 reuses 'archive' for the archive transition and adds 'stale' to AuditAction)`. ACTION ITEM: extend `AuditAction` Literal at `backend/models/audit.py:11-26` to include `"stale"`. Stale-transition audit action becomes `"stale"`.
        - `transition.applied = True`.
    8. After all transitions: `await redis.delete(key_cache_list())` and one `key_cache_item(id)` per touched skill.
    9. Rotate snapshot retention (`rotate_retention(...)`).
    10. Assemble `CuratorRunRecord`; write `run.json` + `REPORT.md` under `curator/runs/{run_id}/`.
    11. Return record. Lock auto-releases.
- **PATTERN**: Module docstring + Cosmos-first ordering from `publish.py:1-13` and `publish.py:89-110`.
- **GOTCHA**: The `_etag` on each doc must be captured *before* the planner runs and passed through — or the executor must re-read each doc when it's about to mutate it (simpler, slightly more expensive — pick this). Re-read happens with `partition_key=skill_id` since `skills` is partitioned by `skill_id`.
- **GOTCHA**: Defense-in-depth on never-delete — there is no call to `skills.delete_item(...)` or `published_container.delete_blob(...)` anywhere in this file. Task 18 enforces this via a CI grep test.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_curator_planner.py -v && uv run pytest backend/tests/integration/test_curator_dry_vs_real.py -v && uv run pytest backend/tests/integration/test_curator_pinned_immune.py -v && uv run pytest backend/tests/integration/test_curator_lock_contention.py -v`

### Task 10: UPDATE `backend/models/audit.py`

- **ADD**: `"stale"` to the `AuditAction` `Literal`.
- **PATTERN**: Mirror line 11-26.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_models.py -v`

### Task 11: CREATE `backend/services/curator_state.py`

- **IMPLEMENT**:
  - `async def pause(*, skills, redis, audit, actor: str) -> None`: write Cosmos shadow doc (`id="_curator_state"`, `skill_id="_curator_state"`, `paused=True`, `paused_by=actor`, `paused_at=now`); `await redis.set(key_curator_pause(), "1")` (no TTL — operator intent). Audit `action="pause"` (extend `AuditAction` if needed; reuse existing if `pause` is acceptable as freeform — propose extending the Literal with `"pause"` and `"resume"`).
  - `async def resume(*, skills, redis, audit, actor) -> None`: replace shadow doc with `paused=False`; `redis.delete(key_curator_pause())`; audit `action="resume"`.
  - `async def is_paused(*, skills, redis) -> bool`: try Redis (`await redis.get(key_curator_pause())`); on miss/error, query Cosmos shadow doc with `partition_key="_curator_state"`. Cache the Cosmos result back into Redis with no TTL.
- **PATTERN**: Cache + Cosmos fallback mirrors `backend/services/catalog.py:38-66`.
- **GOTCHA**: The `_curator_state` doc has a schema that breaks `SkillDoc.model_validate`. Filter it out of every catalog query — add a `c.skill_id != '_curator_state'` clause to every `SELECT * FROM c` against the `skills` container. Audit task: grep for `SELECT * FROM c` in `backend/services/` and add the filter. Alternative (cleaner): put curator state in a dedicated container `system_state` — **decision: use a dedicated container.** Add `SYSTEM_STATE_CONTAINER = "system_state"` to `backend/core/cosmos.py` with PK `/key` and an `ensure_containers` entry. This keeps `skills` queries clean.
- **UPDATE the implementation accordingly** — `_curator_state` doc lives in `system_state` with `id="curator_pause"`, `key="curator_pause"`.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_pause_durability.py -v`

### Task 12: UPDATE `backend/core/cosmos.py`

- **ADD**: `SYSTEM_STATE_CONTAINER = "system_state"`. In `ensure_containers`, add `await db.create_container_if_not_exists(id=SYSTEM_STATE_CONTAINER, partition_key=PartitionKey(path="/key"))`.
- **PATTERN**: Mirror lines 47-65.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_cosmos_bootstrap.py -v`

### Task 13: UPDATE `backend/core/deps.py`

- **ADD**: `def get_system_state_container(db: DatabaseProxy = Depends(get_db)) -> ContainerProxy: return get_container(db, SYSTEM_STATE_CONTAINER)`.
- **PATTERN**: Mirror lines 37-46.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_config.py -v` (and the curator router tests in Task 16 exercise this dep).

### Task 14: CREATE `backend/services/curator_rollback.py`

- **IMPLEMENT**:
  - Module docstring documenting the intentional Blob-first ordering for rollback (and *why* it inverts the rule).
  - `async def rollback(*, snapshot_name: str | None, skills, audit, blob, redis, settings, actor="system:curator") -> RollbackResult`:
    1. Acquire `redis_lock(redis, key_curator_run_lock(), ttl=settings.curator_lock_ttl_seconds)` — same lock as curator pass; mutual exclusion.
    2. Resolve `snapshot_name = snapshot_name or (await list_snapshots(blob, settings))[0]`. Raise `SnapshotNotFound` if none.
    3. Load `manifest = await load_manifest(blob, settings, snapshot_name)`.
    4. Take a pre-rollback snapshot: `pre_name = f"pre-rollback-{utc-iso}"`; `await snapshot_published(blob, settings, run_id=pre_name)`.
    5. For each entry in `manifest.skills` (sorted by skill_id):
        - Restore Blob: `put_published(blob, settings, skill_id=entry.skill_id, version=entry.version, data=tar_member_bytes)`. `tar_member_bytes` comes from extracting `{snapshot_name}/skills.tar.gz`.
        - Restore Cosmos: re-read current doc, set `status=entry.status` (and `bundle.checksum_sha256` etc. from the entry). Write via `replace_with_etag_retry`.
        - `audit_svc.record(audit, skill_id=entry.skill_id, action="rollback", before={"status": current.status}, after={"status": entry.status}, metadata={"snapshot_name": snapshot_name})`.
    6. Invalidate caches.
    7. Write a rollback report under `curator/runs/rollback-{utc-iso}/`.
    8. Return `RollbackResult`.
- **PATTERN**: Lock + audit pattern from `backend/services/publish.py`.
- **GOTCHA**: `replace_with_etag_retry` may fail if Cosmos doc was deleted (it shouldn't be — never-delete invariant — but defense-in-depth). On `CosmosResourceNotFoundError`, re-create via `create_item`.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_rollback_round_trip.py -v`

### Task 15: CREATE `backend/services/janitor.py`

- **IMPLEMENT**: `async def janitor_classifier_queue(*, skills, audit, redis, settings, now=None) -> dict[str, int]`: compute `cutoff = (now or utcnow) - timedelta(seconds=settings.classifier_blpop_timeout_seconds * settings.janitor_classifier_stale_multiplier)`. Query `SELECT * FROM c WHERE c.classifier_status='queued' AND c.uploaded_at < @cutoff` (cross-partition, also exclude `c.skill_id != '_curator_state'`). For each: `await redis.rpush(key_queue_classifier(), doc.id)`; `audit_svc.record(audit, action="classify", actor="system:janitor", metadata={"requeued": True})`. Return `{"requeued": N, "scanned": M}`.
- **PATTERN**: Logging via `bind(actor="system:janitor")`.
- **GOTCHA**: Don't add `"janitor"` as a separate `AuditAction`; reuse `"classify"` with metadata so existing audit queries Just Work.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_janitor_requeue.py -v`

### Task 16: CREATE `backend/services/curator_report.py`

- **IMPLEMENT**: `def render_report(rec: CuratorRunRecord) -> str` — pure function returning a Markdown string. Sections: Header (run id, started/finished, dry-run), Summary table (counts per transition reason), Detail table (`| skill_id | version | before | after | reason | applied |`), Skipped pinned list. `async def persist_report(blob, settings, rec: CuratorRunRecord) -> None`: writes both `run.json` and `REPORT.md` under `{settings.curator_runs_container_prefix}/{rec.run_id}/`. Use the `snapshots` Blob container for this prefix (cheap; no new container) — or alternatively, add `curator_reports_container: str = "curator"` to settings and create it in `ensure_containers`. **Decision: add a `curator` Blob container.** Update `backend/core/blob.py:25-37` to also ensure `settings.curator_reports_container`.
- **PATTERN**: Pure-function renderer is unit-testable via golden file in `backend/tests/unit/test_curator_report.py`.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_curator_report.py -v`

### Task 17: CREATE `backend/api/curator.py`

- **IMPLEMENT**: `router = APIRouter(prefix="/v1/admin/curator", tags=["curator"])`. Endpoints:
  - `POST /pause` → `curator_state.pause(...)` → returns `CuratorStatus`.
  - `POST /resume` → `curator_state.resume(...)` → returns `CuratorStatus`.
  - `POST /run` (`dry_run: bool = Query(False)`) → `curator.execute_pass(dry_run=dry_run, ...)` → returns `CuratorRunRecord`.
  - `POST /rollback` (`id: str | None = Query(None)`) → `curator_rollback.rollback(snapshot_name=id, ...)` → returns `RollbackResult`.
  - `POST /restore/{skill_id}` → restore single archived skill (separate from rollback): copy `archive/{id}/{ver}/bundle.tar.gz` → `published/...`, flip status `archived → approved`, audit `restore`, invalidate cache. Returns `SkillListItem`.
  - `POST /pin/{skill_id}` → flip `pinned=True`, `pinned_by=user.email`, audit `pin`, invalidate cache. Returns `SkillListItem`.
  - `POST /unpin/{skill_id}` → reverse. Returns `SkillListItem`.
  - `GET /status` → returns `CuratorStatus` (paused, lock_held, last_run from latest `run.json` in Blob, schedule fields from config).
  - `POST /janitor` → invoke janitor. Returns `{"requeued": int, "scanned": int}`.
- **PATTERN**: Mirror `backend/api/admin.py:31-115` exactly: `_require_admin = require_role("admin")`; every handler `Depends(_require_admin)`.
- **IMPORTS**: As needed; include `from fastapi import Query`.
- **GOTCHA**: `last_run` from Blob requires listing `curator/runs/` prefix and finding the lexicographically-largest (UTC-iso) folder. Cache result for 5s in Redis to keep `/status` cheap.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_endpoints.py -v` (create file; one test per endpoint, smoke-level).

### Task 18: CREATE `backend/tests/unit/test_never_delete_invariant.py`

- **IMPLEMENT**: A grep-style static check using `pathlib`:
  ```python
  import pathlib, re
  CURATOR_FILES = list(pathlib.Path("backend").rglob("services/curator*.py")) + \
                  list(pathlib.Path("backend").rglob("services/snapshot.py")) + \
                  list(pathlib.Path("backend").rglob("services/janitor.py")) + \
                  list(pathlib.Path("backend").rglob("api/curator.py"))

  FORBIDDEN = [r"\.delete_item\s*\(", r"\.delete_blob\s*\(", r"delete_container\s*\("]

  def test_no_destructive_calls_in_curator_code():
      for path in CURATOR_FILES:
          src = path.read_text()
          for pat in FORBIDDEN:
              assert not re.search(pat, src), f"forbidden destructive call {pat!r} found in {path}"
  ```
- **PATTERN**: Mirror enforcement-test style (no analog yet; this is the new pattern).
- **GOTCHA**: If a legitimate use of `delete_item` ever lands (e.g. `system_state` shadow doc cleanup), add a narrow allowlist with a comment justifying it. Default posture: deny.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_never_delete_invariant.py -v`

### Task 19: CREATE `backend/workers/curator.py`

- **IMPLEMENT**: CLI with `argparse` subcommands `{run, dry-run, rollback, restore, status, schedule}`. `schedule` starts an `AsyncIOScheduler` with a cron trigger from `settings.curator_schedule_cron` invoking `execute_pass(dry_run=False, ...)`. Signal handlers + `asyncio.Event` for graceful shutdown.
- **PATTERN**: Mirror `backend/workers/classifier.py:102-168` (signal handlers, lifespan, telemetry init at top of `main()`).
- **VALIDATE**: `python -m backend.workers.curator --help` runs without error; `python -m backend.workers.curator dry-run` against the live stack produces a report under `curator/runs/{ts}/REPORT.md` in Azurite.

### Task 20: CREATE `backend/workers/janitor.py`

- **IMPLEMENT**: Tiny CLI: `python -m backend.workers.janitor [--once|--loop]`. `--once` runs the sweep and exits. `--loop` runs every `settings.classifier_blpop_timeout_seconds * settings.janitor_classifier_stale_multiplier / 2` seconds.
- **PATTERN**: Mirror classifier worker shape.
- **VALIDATE**: `python -m backend.workers.janitor --once` runs cleanly when no stale docs exist.

### Task 21: UPDATE `backend/app.py`

- **ADD**: `from backend.api import curator as curator_router`; `app.include_router(curator_router.router)`. Also extend `ensure_containers` call site no-op (already idempotent now that `SYSTEM_STATE_CONTAINER` is in `ensure_containers`). Also add `curator` Blob container to the list in `backend/core/blob.py:25-37`.
- **PATTERN**: Mirror existing `include_router` calls at lines 95-98.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_endpoints.py -v`

### Task 22: UPDATE `backend/core/blob.py`

- **ADD**: `curator_reports_container: str = "curator"` was added in Task 1. Here in `ensure_containers`, add it to the tuple at line 27-31.
- **PATTERN**: Mirror lines 25-37.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_cosmos_bootstrap.py -v` (this test also verifies Blob container creation via a smoke call).

### Task 23: UPDATE `Makefile`

- **ADD** targets:
  - `curator: ; $(PY) -m backend.workers.curator schedule`
  - `curator-run: ; $(PY) -m backend.workers.curator run`
  - `curator-dry-run: ; $(PY) -m backend.workers.curator dry-run`
  - `curator-rollback: ; $(PY) -m backend.workers.curator rollback`
  - `janitor: ; $(PY) -m backend.workers.janitor --loop`
- **PATTERN**: Mirror `worker:` at `Makefile:37-38`.
- **VALIDATE**: `make curator-dry-run` succeeds against the live stack.

### Task 24: CREATE `backend/tests/unit/test_curator_planner.py`

- **IMPLEMENT**: Truth table covering: (a) just-uploaded skill (last_loaded_at=None, uploaded_at=now) → no transition; (b) approved+last_loaded_at=now-31d → stale; (c) stale+last_loaded_at=now-91d → archived; (d) approved+pinned+last_loaded_at=now-100d → skipped; (e) approved+last_loaded_at=None+uploaded_at=now-91d → archived (treat unused-since-upload as archive eligible after grace).
- **PATTERN**: Pure-function tests; no fixtures beyond constructed `SkillDoc` instances.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_curator_planner.py -v`

### Task 25: CREATE `backend/tests/unit/test_curator_report.py`

- **IMPLEMENT**: Golden-file test: build a fixed `CuratorRunRecord`, call `render_report`, compare to a checked-in expected `.md` string. Update the golden via env var (e.g. `UPDATE_GOLDEN=1`).
- **VALIDATE**: `uv run pytest backend/tests/unit/test_curator_report.py -v`

### Task 26: CREATE `backend/tests/unit/test_snapshot_deterministic.py`

- **IMPLEMENT**: Two calls to the tar-building helper used inside `snapshot.py` (extract it as `_build_snapshot_tar(files: dict[str, bytes]) -> bytes` if needed) with the same input — assert sha256 equality.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_snapshot_deterministic.py -v`

### Task 27: CREATE `backend/tests/unit/test_usage_counter_math.py`

- **IMPLEMENT**: Unit test for `recompute_loaders_30d` using a fake Cosmos container that returns a fixed event list; assert distinct-loader count matches.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_usage_counter_math.py -v`

### Task 28: CREATE `backend/tests/integration/test_usage_endpoint.py`

- **IMPLEMENT**: Seed an approved skill (mirror `_cleanup` helper from `test_e2e_happy_path.py:61-83`). POST 3 usage events. Assert: 3 rows in `usage_events`, `usage.load_count == 3` on the skill, `usage.last_loaded_at` updated, `cache:skills:list:v1` evicted, `cache:skills:item:{id}` evicted.
- **PATTERN**: Mirror `test_e2e_happy_path.py:50-179`.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_usage_endpoint.py -v`

### Task 29: CREATE `backend/tests/integration/test_janitor_requeue.py`

- **IMPLEMENT**: Insert a synthetic skill doc with `classifier_status="queued"` and `uploaded_at = now - 1h`. Drain `queue:classifier`. Run `janitor_classifier_queue`. Assert the doc's id is on the queue.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_janitor_requeue.py -v`

### Task 30: CREATE `backend/tests/integration/test_curator_lock_contention.py`

- **IMPLEMENT**: Mirror `backend/tests/integration/test_redis_lock.py:25-33` but use `key_curator_run_lock()`. Start two `execute_pass(dry_run=True)` tasks concurrently with `asyncio.gather(..., return_exceptions=True)`; assert exactly one raised `LockUnavailable`.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_lock_contention.py -v`

### Task 31: CREATE `backend/tests/integration/test_curator_dry_vs_real.py`

- **IMPLEMENT**:
  1. Seed three approved skills with deterministic `last_loaded_at` (fresh, stale-eligible, archive-eligible).
  2. Snapshot Blob state (sha256 of every blob in `published/`).
  3. Call `execute_pass(dry_run=True, now=fixed)` → capture `transitions_dry`.
  4. Confirm Blob state and Cosmos status unchanged (no mutations).
  5. Call `execute_pass(dry_run=False, now=fixed)` → capture `transitions_real`.
  6. Assert `transitions_dry == transitions_real` modulo `applied` field (set to False vs True).
  7. Assert audit rows for the real run exactly cover the non-pinned non-steady-state transitions.
- **PATTERN**: `test_e2e_happy_path.py` shape.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_dry_vs_real.py -v`

### Task 32: CREATE `backend/tests/integration/test_curator_rollback_round_trip.py`

- **IMPLEMENT**:
  1. Seed five approved skills; record `sha256` set of every blob in `published/`.
  2. Call `execute_pass(dry_run=False)` configured so some get archived (set their `last_loaded_at` to 91d ago).
  3. List `snapshots/` — pick latest.
  4. Call `rollback(snapshot_name=latest)`.
  5. Re-list `published/` and compute sha256 set. **Assert equal to original.**
  6. Assert one `audit` row with `action="rollback"` per restored skill.
  7. Assert a `pre-rollback-{utc-iso}` snapshot folder also exists.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_rollback_round_trip.py -v`

### Task 33: CREATE `backend/tests/integration/test_curator_pinned_immune.py`

- **IMPLEMENT**: Seed three approved skills, pin one (`POST /pin/{id}`). Force-age all three to 91d unused. Run `execute_pass(dry_run=False)`. Assert pinned skill is still `approved` in Cosmos and its bytes are still in `published/`; the other two are `archived` with bytes copied to `archive/`.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_pinned_immune.py -v`

### Task 34: CREATE `backend/tests/integration/test_curator_pause_durability.py`

- **IMPLEMENT**: `POST /pause`. Flush Redis (`await redis.flushdb()`). Call `POST /run` → expect `CuratorPaused` (HTTP 409) — i.e. the pause survived Redis flush because of the Cosmos shadow doc fallback.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_curator_pause_durability.py -v`

### Task 35: UPDATE `backend/tests/integration/test_redis_down_fallback.py`

- **ADD**: A case where `/v1/admin/curator/status` is called with Redis unreachable — assert the endpoint still returns 200 by falling back to Cosmos for pause state and to Blob for last_run.
- **PATTERN**: Mirror existing fallback assertions.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_redis_down_fallback.py -v`

### Task 36: UPDATE `pyproject.toml`

- **ADD**: `apscheduler>=3.10` to `[project] dependencies`.
- **VALIDATE**: `uv sync && uv run python -c "import apscheduler; print(apscheduler.__version__)"`

### Task 37: DOC UPDATE — add a `## Curator (M2)` section to `AGENTS.md` §5

- **IMPLEMENT**: A short paragraph linking to this plan, calling out the never-delete CI gate (Task 18), and updating the "Key Files" table to add `backend/services/curator.py` and `backend/services/curator_rollback.py`.
- **VALIDATE**: Manual review.

---

## TESTING STRATEGY

The project's pytest layout (`backend/tests/{unit,integration}`) and the `integration` marker auto-skip behavior (`backend/tests/conftest.py:31-42`) are the existing baseline. M2 follows the same split.

### Unit Tests

In `backend/tests/unit/`. Pure-function coverage:
- `test_curator_planner.py` — every branch of `plan_transitions` truth table.
- `test_curator_report.py` — golden file for `render_report`.
- `test_snapshot_deterministic.py` — same input bytes always produce the same tar.
- `test_usage_counter_math.py` — `recompute_loaders_30d` math.
- `test_never_delete_invariant.py` — static grep check.
- Extend `test_errors.py`, `test_models.py`, `test_redis_cache_keys.py`, `test_config.py` for the new symbols.

Run: `uv run pytest backend/tests/unit -v`.

### Integration Tests

In `backend/tests/integration/`. Carry `pytestmark = pytest.mark.integration`. Each test does its own `_cleanup` (mirror `test_e2e_happy_path.py:61-83`).

- `test_usage_endpoint.py`
- `test_janitor_requeue.py`
- `test_curator_lock_contention.py`
- `test_curator_dry_vs_real.py` — **the dry-run/real-run equality test**.
- `test_curator_rollback_round_trip.py` — **the byte-for-byte rollback test**.
- `test_curator_pinned_immune.py` — **the pinning invariant test**.
- `test_curator_pause_durability.py`
- `test_curator_endpoints.py` — one smoke test per admin endpoint.
- `test_snapshot_round_trip.py` — snapshot → load_manifest → download_snapshot_tar → checksum equality.
- `test_cosmos_etag_retry.py` — concurrent counter writes, retry succeeds.
- Extend `test_redis_down_fallback.py` for `GET /status` with Redis down.

Run: `make up && uv run pytest backend/tests/integration -v -m integration`.

### Edge Cases

- A skill with `last_loaded_at=None` and `uploaded_at < now - archive_days` → archived (grace expired).
- A skill with `status="rejected"` → never considered by planner.
- A skill mid-publish (lock held by `publish.py`) → curator pass still works on other skills; the held doc may get an `_etag` conflict and retry succeeds on the next read.
- Cosmos returns 412 three times on a counter increment → final attempt raises; integration test asserts HTTP 503 from the usage endpoint with `error_code="INTERNAL_ERROR"` (or define a new code `COUNTER_CONFLICT`).
- Snapshot tar missing one of the manifest entries (manual tampering) → rollback raises `RestoreFailed` listing the missing entries.
- Two `POST /run` calls in flight simultaneously → second returns HTTP 423 with `error_code="LOCK_UNAVAILABLE"`.
- `POST /rollback` while `POST /run` holds the lock → 423.
- `POST /pause` then `POST /run` → 409 `CURATOR_PAUSED`.
- Skill is pinned mid-pass: planner already snapshotted the un-pinned state; executor re-reads the doc before each mutation and observes the new `pinned=True`. Decision: respect the latest state — skip the transition. Document this in the planner docstring.

---

## VALIDATION COMMANDS

Execute every command to ensure zero regressions and 100% feature correctness.

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
uv run pytest backend/tests/integration -v -m integration
```

### Level 4: Manual Validation

```bash
# Bring up the stack
make up && make api &           # FastAPI
make worker &                   # Classifier worker
make curator &                  # Curator scheduler

# Seed a skill
make seed

# Post some usage events
curl -X POST http://localhost:8000/v1/skills/sample-skill/usage \
  -H 'X-User-Email: alice@org' \
  -H 'Content-Type: application/json' \
  -d '{"loader_id":"hermes-runtime-42","context":{"session":"abc"}}'

# Run a dry-run pass
curl -X POST 'http://localhost:8000/v1/admin/curator/run?dry_run=true' \
  -H 'X-User-Email: admin@org'

# Inspect the report
az storage blob list --connection-string "$BLOB_CONNECTION_STRING" \
  --container-name curator --prefix runs/ --output table

# Run a real pass
curl -X POST 'http://localhost:8000/v1/admin/curator/run' \
  -H 'X-User-Email: admin@org'

# Roll it back
curl -X POST 'http://localhost:8000/v1/admin/curator/rollback' \
  -H 'X-User-Email: admin@org'

# Pin a skill
curl -X POST 'http://localhost:8000/v1/admin/curator/pin/sample-skill' \
  -H 'X-User-Email: admin@org'

# Pause + verify status
curl -X POST 'http://localhost:8000/v1/admin/curator/pause' -H 'X-User-Email: admin@org'
curl 'http://localhost:8000/v1/admin/curator/status' -H 'X-User-Email: admin@org'

# Janitor sweep
curl -X POST 'http://localhost:8000/v1/admin/curator/janitor' -H 'X-User-Email: admin@org'
```

### Level 5: Additional Validation (Optional)

```bash
# Hostile: try to delete a published blob directly via the SDK; rollback restores it.
# (Hand-craft via az cli, then run rollback, then diff sha256.)

# Determinism: run dry-run twice, diff the two REPORT.md files — must be identical.
uv run python -m backend.workers.curator dry-run > /tmp/r1.md
uv run python -m backend.workers.curator dry-run > /tmp/r2.md
diff /tmp/r1.md /tmp/r2.md && echo "DETERMINISTIC OK"
```

---

## ACCEPTANCE CRITERIA

- [ ] `POST /v1/skills/{id}/usage` writes a `usage_events` row, bumps counters atomically, invalidates both cache keys.
- [ ] Curator planner produces deterministic transitions for a given input snapshot (asserted by `test_curator_dry_vs_real.py`).
- [ ] Real-run is preceded by a snapshot at `snapshots/{utc-iso}/skills.tar.gz` + `manifest.json`.
- [ ] Pinned skills are never transitioned by a curator pass (`test_curator_pinned_immune.py`).
- [ ] Rollback restores Blob bytes byte-for-byte and Cosmos status to the snapshot state (`test_curator_rollback_round_trip.py`).
- [ ] Rollback itself snapshots first, so it is reversible.
- [ ] All admin endpoints under `/v1/admin/curator/` are gated by `require_role("admin")`.
- [ ] Concurrent `execute_pass` calls — only one runs; the other raises `LockUnavailable` (HTTP 423).
- [ ] Pause state survives a Redis flush (Cosmos shadow doc fallback).
- [ ] Janitor sweep re-queues stale `classifier_status=queued` docs.
- [ ] Per-run report written to `curator/runs/{utc-iso}/{run.json, REPORT.md}`.
- [ ] `test_never_delete_invariant.py` passes (no `delete_item` or `delete_blob` calls anywhere in curator code).
- [ ] All existing tests still pass (no M0/M1 regressions).
- [ ] `uv run ruff check .` and `uv run ruff format --check .` clean.
- [ ] Full integration suite passes against the local docker-compose stack.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order.
- [ ] Each task validation passed immediately.
- [ ] All validation commands executed successfully.
- [ ] Full test suite passes (unit + integration).
- [ ] No linting or type checking errors.
- [ ] Manual validation walkthrough confirms the feature works end-to-end on the local stack.
- [ ] `AGENTS.md` §5 updated with a pointer to this plan and a note about the never-delete CI gate.
- [ ] Acceptance criteria all met.
- [ ] Code reviewed for adherence to the four Redis rules and the never-delete invariant.

---

## NOTES

**Why we don't delete the source bytes from `published/` on archive.** Defense-in-depth. `archived` status in Cosmos already makes the skill disappear from every catalog query (which filter `status='approved'`). Leaving the source bytes in `published/` for the lifetime of the run gives us a free second copy until a future garbage sweep (post-M2) actively reclaims space. This trades a small amount of storage for a meaningful safety margin.

**Why rollback inverts the Cosmos-first rule.** For *forward* writes (publish, archive, classify), Cosmos-first guarantees the SoR never points at bytes that aren't there. For *backward* writes (rollback), we need bytes back in place *before* Cosmos points at them again — otherwise a partial failure mid-restore leaves Cosmos pointing at the snapshot version and the actual bytes still on the previous (potentially also valid) blob. Blob-first means Cosmos always points at bytes that exist. Documented in `backend/services/curator_rollback.py` module docstring.

**Why pause-state lives in both Cosmos and Redis.** Pure-Redis would violate rule #3 (no infinite-TTL keys) and rule #1 (Cosmos is SoR). Pure-Cosmos would make every curator tick do a Cosmos read just to check pause. Solution: Cosmos is truth, Redis is the hot-path cache. Reads check Redis first, fall back to Cosmos on miss/error (rule #2). The Redis key intentionally has no TTL because operator intent persists; the Cosmos shadow doc is the durable backstop.

**Why an in-process scheduler (APScheduler) for local + Azure Functions timer for prod.** The curator service function `execute_pass(...)` is the same in both environments. APScheduler is the local-dev wrapper; an Azure Functions timer trigger is the prod wrapper. The wrapper is thin (one call), keeping AGENTS.md §6 (local-first) honest while making prod simpler.

**Why `loaders_30d` is recomputed per event rather than incrementally maintained.** Incremental maintenance of a distinct-count window requires either a per-loader-per-skill rate-limiter table or a HyperLogLog sketch — both are out of scope for M2 and Cosmos doesn't natively support them. Per-event recomputation is a small partitioned query (`SELECT VALUE COUNT(DISTINCT c.loader_id) ... WHERE c.skill_id=@id`), bounded by 90 days × max events. If this becomes a hotspot in M4, swap to a Redis HyperLogLog (separate concern; recomputed nightly into Cosmos by the curator).

**Why we add `system_state` rather than namespace inside `skills`.** Mixing types in a Cosmos container forces every existing query to filter the off-type rows or risk `model_validate` failures. The cost of one extra small container is negligible against the cost of polluting every query in the codebase.

**Why no UI in M2.** PRD §12.M2 doesn't require it; M3 adds the LLM review pass which is the first thing that needs a UI tab (consolidation suggestions). Admin operations in M2 are infrequent and tolerable via CLI/curl. UI for pin/unpin and status lands alongside the M3 review tab.

**Confidence score for one-pass implementation: 7.5/10.** The deterministic-planner and rollback-round-trip parts are well-bounded but have many small details (Cosmos optimistic concurrency, deterministic tar bytes, blob copy semantics, lock semantics across two services, audit row counts) where a missed detail produces a subtle test failure rather than an obvious crash. The integration tests are designed to surface these on first run.
