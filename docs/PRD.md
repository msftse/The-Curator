# Agentic Skill Hub — Product Requirements Document

**Status:** Draft v0.2
**Owner:** Michael Liav
**Last updated:** 2026-05-16

---

## 1. Executive Summary

Agentic Skill Hub is an internal web platform for submitting, reviewing, publishing, and maintaining reusable agent skills (SKILL.md bundles compatible with Hermes Agent and similar agentic frameworks). It turns ad-hoc, scattered prompt and procedure knowledge — currently spread across individual laptops, Notion pages, and copy-pasted prompts — into a governed, queryable, version-tracked library.

The platform routes every submission through an automated classifier agent (auto-tags category, tags, quality, summary, duplicate candidates), a manager review queue (approve/reject with overrides), and an immutable publish pipeline. Approved skills are exposed via a public read-only REST API that any agent runtime can hit to discover and download skills. A background curator process maintains the published catalog over time — tracking usage, archiving stale entries, surfacing consolidation candidates — with hard invariants that prevent accidental data loss.

**MVP goal:** Ship an end-to-end flow (upload → classify → approve → publish → list/download) running on local emulators within two weeks, with the architecture and invariants in place to scale into Azure without rework.

---

## 2. Mission

**Mission:** Become the single source of truth for shared, sanctioned agent skills inside the organization — governed, versioned, observable, and safe to maintain at scale.

**Core principles:**
1. **Cosmos is the source of truth.** Every durable write hits Cosmos first. Redis and Blob are regenerable from Cosmos plus original upload payloads.
2. **Cache, don't double-write.** Redis is a cache and coordination tool, never an authoritative store.
3. **Never silently destroy.** The curator can archive, suggest, and snapshot — but never deletes. Pinning is absolute.
4. **Classifier suggests, manager decides.** Automation accelerates review; it does not replace it.
5. **Local-first dev loop.** The full system runs on emulators with zero Azure spend so contributors can iterate without cloud cost or credentials.

---

## 3. Target Users

| Persona | Description | Technical Comfort | Key Needs / Pain Points |
|---------|-------------|-------------------|-------------------------|
| Contributor | Anyone in the org submitting a skill | Mixed (engineers, PMs, ops) | Easy upload, clear status visibility, fast feedback from classifier |
| Manager | Approves/rejects pending skills | High | Triage queue, classifier output, override controls, no footguns |
| Consumer (agent runtime) | Hermes or other agents pulling skills programmatically | N/A (machine) | Stable REST API, low-latency catalog, signed-URL downloads |
| Admin | Operates the hub | Very high | Configure curator, run rollbacks, audit access, manage pins |

---

## 4. MVP Scope

### Core Functionality
- ✅ Web UI: upload form, my-submissions view, manager review queue
- ✅ Backend API: upload, list, get, download, usage, admin
- ✅ Classifier agent (async, queue-backed) auto-running on every upload
- ✅ Manager approval flow → publish bundle to Blob Storage
- ✅ Public read-only catalog API for agent runtimes
- ✅ Curator: usage tracking, deterministic stale/archive transitions, snapshots, rollback, pinning
- ✅ Audit log for all state transitions
- ✅ Versioned immutable bundle artifacts in Blob

### Technical
- ✅ Cosmos DB as system of record (skill metadata, audit, usage events)
- ✅ Redis as cache + classifier queue + locks (TTL everywhere, AOF on the queue)
- ✅ Azure Blob Storage for immutable bundle bytes and curator snapshots
- ✅ Cosmos-first write discipline with Redis invalidation after success
- ✅ Cosmos fallback on every Redis read path

### Integration
- ✅ Entra ID OIDC for humans (POC uses header stub)
- ✅ API keys for agent runtimes
- ✅ Bicep templates for Azure resources
- ✅ GitHub Actions CI/CD

### Deployment
- ✅ Local dev: docker-compose with Cosmos DB emulator + Azurite + redis:7
- ✅ Prod: Azure (Cosmos serverless, Blob LRS, Cache for Redis, Functions/App Service)

### ❌ Out of Scope (v1)
- ❌ In-browser skill editor (upload-only; edits = new version)
- ❌ Multi-tenant / org isolation (single org)
- ❌ Public marketplace / external publishing
- ❌ Skill execution / sandbox testing inside the hub
- ❌ SSO production hardening (POC uses header stub)
- ❌ Billing, quotas, advanced rate limiting beyond basic abuse prevention
- ❌ Replacement for per-user local skill directories (hub is for shared/sanctioned skills only)

---

## 5. User Stories

**Contributor**
- As a contributor, I want to upload a SKILL.md (drag-drop) and see an immediate auto-classification preview, so I understand how the system interpreted my skill.
- As a contributor, I want to submit additional bundle files (`references/`, `templates/`, `scripts/`) alongside the SKILL.md, so I can ship complete skills.
- As a contributor, I want to see the status of my submission (pending / classified / approved / rejected) and any manager feedback, so I know what to do next.

**Manager**
- As a manager, I want to open a review queue sorted by submission time or classifier quality score, so I can triage efficiently.
- As a manager, I want to view the rendered SKILL.md and bundle file tree inline, so I don't have to download anything to review.
- As a manager, I want to override the classifier's category/tags before approving, so the catalog stays clean.
- As a manager, I want to see Defender status and findings in the review queue, approve only after a clean scan, override flagged findings with a required justification, or quarantine malicious submissions, so unsafe bundles cannot be published accidentally.

**Consumer (agent runtime)**
- As an agent runtime, I want to `GET /v1/skills?category=devops`, so I can discover approved skills filtered to my needs.
- As an agent runtime, I want to download a skill bundle as a tar.gz via a short-lived signed URL embedded in a copyable prompt, so I avoid hitting the app tier for bytes and the link expires quickly.
- As an agent runtime, I want to `POST /v1/skills/{id}/usage` when I load a skill, so the curator has real data to work with.

**Admin**
- As an admin, I want to pause the curator and run dry-run passes, so I can review what would change before mutating anything.
- As an admin, I want to pin a skill so the curator never touches it, regardless of usage.
- As an admin, I want to roll back a curator pass using the most recent snapshot, so a bad pass is recoverable in minutes.

---

## 6. Core Architecture & Patterns

### High-Level Diagram

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Next.js Web UI  │─────│   FastAPI API    │─────│   Cosmos DB      │
│  (contributor +  │     │  (REST + auth)   │◄───►│   (SoR: all      │
│   manager views) │     │                  │     │    metadata,     │
└──────────────────┘     └─┬──────┬──────┬──┘     │    audit, usage) │
                           │      │      │        └──────────────────┘
                           │      │      │
              ┌────────────▼──┐ ┌─▼──────▼───┐  ┌─────────────────┐
              │  Redis        │ │ Blob       │  │   Curator       │
              │  (cache +     │ │ Storage    │  │   (background   │
              │   classifier  │ │ (approved  │  │    maintenance) │
              │   queue +     │ │  bundles,  │  └─────────────────┘
              │   locks)      │ │  snapshots)│
              └───────┬───────┘ └────────────┘
                      │
              ┌───────▼────────┐
              │  Classifier    │
              │  Worker        │
              │  (BLPOP queue) │
              └────────────────┘
```

### Storage Split (Final Decision — Non-negotiable)

**Cosmos DB (NoSQL) — system of record. All durable writes hit Cosmos first.**
- Skill metadata (pending → classified → approved → archived)
- Audit log (append-only container)
- Usage events (raw, TTL 90 days) + aggregated counters
- Pinning state, classification, version history

**Redis (Azure Cache for Redis) — cache + ephemeral coordination only. Never the only copy of anything.**
- Hot catalog list responses (60s TTL, invalidated on publish/archive)
- Single-skill metadata lookups (5min TTL, invalidated on update)
- Classifier job queue (Redis LIST + BLPOP; AOF persistence enabled)
- Rate limit counters (sliding window with TTL)
- Web UI session tokens
- Distributed locks for publish/curator (SET NX with TTL — prevents double-publish)

**Azure Blob Storage — immutable artifact bytes only.**
- Approved bundle tar.gz files at `published/{skill_id}/{version}/bundle.tar.gz`
- Curator snapshots at `snapshots/{utc-iso}/skills.tar.gz`
- Archived skill bundles at `archive/{skill_id}/{version}/`

**Why this split:** Cosmos handles query/filter/index (categories, tags, search, audit). Redis handles hot-path latency, queueing, and distributed locks. Blob handles cheap immutable artifact hosting with signed URLs and CDN-frontable downloads. Cosmos is the only source of truth; Redis is regenerable from Cosmos; Blob is regenerable from Cosmos plus the original upload payload.

### Non-negotiable Redis Rules

1. **Writes ALWAYS hit Cosmos first.** Redis invalidation happens *after* the Cosmos write succeeds. Never write to Redis as the source of truth.
2. **Cache misses are normal, not errors.** Every Redis read path must have a Cosmos fallback. If Redis is down, the app is slower — not broken.
3. **TTL everything in Redis.** No infinite-lived keys. Worst case the cache rebuilds in N seconds.
4. **The classifier queue is the one place Redis temporarily holds in-flight data.** Mitigation: AOF persistence is enabled, the upload handler writes the pending doc to Cosmos *before* pushing to the Redis queue, and a janitor sweep re-queues lost messages by scanning Cosmos for `classifier_status=pending` docs older than a threshold.

### What Lives Where

| Concern | Store | Notes |
|---------|-------|-------|
| Skill metadata (pending → approved → archived) | Cosmos | Single doc per skill version |
| Audit log | Cosmos | Append-only container, no updates |
| Usage events (raw) | Cosmos | TTL 90 days |
| Usage counters (aggregated) | Cosmos | Updated on event ingest |
| Pinning state, classification, version history | Cosmos | On the skill doc |
| Approved bundle bytes | Blob | Immutable, versioned |
| Curator snapshots | Blob | tar.gz of skills tree before each pass |
| Archived skill bundles | Blob | `archive/` prefix, recoverable |
| Hot catalog list responses | Redis | 60s TTL, invalidated on publish/archive |
| Single-skill metadata lookups | Redis | 5min TTL, invalidated on update |
| Classifier job queue | Redis | List + BLPOP; AOF persistence enabled |
| Rate limit counters | Redis | Sliding window with TTL |
| Web UI session tokens | Redis | TTL = session lifetime |
| Distributed locks (publish, curator) | Redis | SET NX with TTL; prevents double-publish |

### Lifecycle of a Skill

```
upload → pending (Cosmos) → [classifier runs] → classified (Cosmos)
       → [manager reviews] → approved → [publish job] → published (Blob + Cosmos)
                          → rejected (Cosmos, terminal)

published → active → stale (no usage 30d) → archived (no usage 90d, in Blob archive/)
         ↳ pinned skills bypass auto-transitions
```

### Suggested Directory Structure

```
agentic-skill-hub/
├── backend/                # FastAPI app
│   ├── api/                # Route modules: skills, usage, admin, auth
│   ├── core/               # Config, deps, cosmos/redis/blob clients
│   ├── services/           # Business logic (upload, publish, curator, audit)
│   ├── workers/            # Classifier worker, curator worker, janitor
│   └── tests/
├── frontend/               # Next.js 14 + Tailwind
│   ├── app/                # Upload, my-submissions, review queue, admin
│   └── components/
├── infra/                  # Bicep templates
├── scripts/                # Dev tooling, seed data
├── docker-compose.yml      # Cosmos emulator + Azurite + redis:7
└── docs/
    └── PRD.md
```

---

## 7. Tools / Features

### 7.1 Upload
- Accepts: single SKILL.md, .zip/.tar.gz bundle, or multi-file form upload
- Validates YAML frontmatter parses; required fields (`name`, `description`) present; markdown body non-empty
- Rejects malformed uploads before they hit Cosmos
- Max bundle size: 10MB (v1)
- Persists the pending doc to Cosmos, then enqueues the classifier job in Redis

### 7.2 Classifier Agent
- Triggered on every successful upload (async via Redis queue, BLPOP worker)
- Reads SKILL.md and outputs:
  - `category` (single, from a controlled taxonomy: devops, mlops, productivity, social-media, research, creative, …)
  - `tags` (free-form, max 8)
  - `quality_score` (0–100; heuristic + LLM-assessed clarity, completeness, trigger conditions, pitfalls)
  - `summary` (one sentence)
  - `duplicate_candidates` (list of existing approved skill IDs that look similar)
- Writes results back to the same Cosmos doc
- Failure mode: timeout → doc stays pending with `classifier_status=failed`; manager classifies manually

### 7.3 Review Queue
- Paginated list of pending skills, sortable by submission time or classifier quality score
- Per-skill detail: rendered markdown, file tree, editable classifier output, uploader info
- Per-skill Defender panel: status, severity, model, timestamp, and structured findings are visible inline to admins
- Actions: approve, reject (reason required), edit classification, requeue classification (`Classify now`), rescan Defender (`Rescan defender` from skill detail)
- Approval is blocked while Defender is `pending`, `scanning`, or `failed`; flagged medium/high/critical findings require an audit-logged override justification or quarantine
- Bulk approve gated behind per-skill checkboxes (no "approve all" footgun)

### 7.4 Publish
- On approve: background job packages bundle as immutable tar.gz, uploads to Blob at versioned path, writes blob URL + checksum to Cosmos, flips status to `approved`
- Versioning: every approval creates a new version; old versions remain downloadable
- Idempotent: re-running publish for the same version is a no-op
- Guarded by a Redis distributed lock (`SET NX` with TTL) to prevent double-publish

### 7.5 Public Catalog API
- `GET /v1/skills` — list approved skills (filter by category, tag, status)
- `GET /v1/skills/{id}` — metadata for one skill
- `GET /v1/skills/{id}/versions` — version history
- `GET /v1/skills/{id}/download` — one-minute signed URL to bundle tar.gz
- `POST /v1/skills/{id}/usage` — agent reports load/use event (auth required)
- All endpoints return JSON, paginate cursor-style, include rate-limit headers

### 7.6 Curator
- Scheduled (configurable; default daily off-peak)
- **Phase 1 — Deterministic transitions:** no loads in 30 days → `stale`; no loads in 90 days → `archived` (blob moved to `archive/` prefix; Cosmos status flipped)
- **Phase 2 — LLM review pass:** aux-model agent surveys active skills, proposes consolidations of near-duplicates, flags drift (deprecated commands, stale references), opens "curator suggestions" tickets for manager review
- Hard invariants:
  - **Never auto-deletes** — worst case is archival, which is recoverable
  - **Pinned skills are immune** to all auto-transitions and curator suggestions
  - **Snapshot before every real pass** — full tar.gz of published Blob tree, retain N (default 5)
  - Dry-run mode produces a report with no mutations
- Admin commands: `pause`, `resume`, `run --dry-run`, `run`, `rollback`, `pin`, `unpin`, `restore`

### 7.7 Audit Log
- Every state transition (upload, classify, approve, reject, publish, archive, pin, restore, rollback) writes an immutable record to Cosmos
- Queryable by skill ID, actor, action type, time range
- Retention: indefinite (v1)

---

## 8. Technology Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | **FastAPI** (Python 3.12) | Matches Hermes ecosystem, reuse skill validators, fast iteration |
| Frontend | **Next.js 14** + Tailwind | Solid defaults, SSR fine for internal tool, easy auth |
| Database (SoR) | **Azure Cosmos DB for NoSQL** | JSON document model + global indexing; user-specified |
| Cache + queue | **Azure Cache for Redis** | Hot-path reads, classifier queue, rate limits, distributed locks; AOF enabled |
| Object storage | **Azure Blob Storage** | Cheap immutable artifacts, signed URLs, CDN-frontable |
| Background jobs | **Azure Functions** (prod), Python worker process (local dev) | Async classifier, publish, curator |
| Classifier agent | Small aux-model subagent (Hermes-style pattern) | Consistent with org's existing agent infra |
| Auth | **Entra ID (OIDC)** for humans, API keys for agents | Standard Azure stack; POC uses header stub |
| Local dev | Cosmos DB emulator + Azurite + `redis:7` container | Zero Azure spend for POC |
| Infra-as-code | **Bicep** | First-class Azure support |
| CI/CD | GitHub Actions | Standard org tooling |

### Cost Note (POC scale, monthly estimate)

| Service | Tier | ~Cost |
|---------|------|-------|
| Cosmos DB | Serverless | ~$5–25 |
| Blob Storage | LRS, cool tier for archive | ~$1–5 |
| Azure Cache for Redis | Basic C0 (POC) / Standard C0 (prod) | ~$16 / ~$40 |
| App Service / Functions | Consumption | ~$0–20 |
| **Total** | | **< $100/mo for POC** |

Redis is a rounding error. Cosmos + Blob dominate. Scale knobs are well understood.

---

## 9. Security & Configuration

### Authentication / Authorization
- **Humans:** Entra ID OIDC. POC ships a header stub (`X-User-Email`) for local dev.
- **Agent runtimes:** API keys, issued per-runtime, revocable, rate-limited.
- **Role enforcement:** Contributor / Manager / Admin enforced server-side on every protected endpoint.

### Configuration Management
- All secrets via environment variables (12-factor); production secrets in Azure Key Vault.
- Required env vars (illustrative):
  - `COSMOS_ENDPOINT`, `COSMOS_KEY`, `COSMOS_DB_NAME`
  - `REDIS_URL`, `REDIS_PASSWORD`
  - `BLOB_ACCOUNT_URL`, `BLOB_SAS_OR_KEY`
  - `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET`
  - `CLASSIFIER_MODEL`, `CURATOR_MODEL`
  - `AUTH_MODE` (`stub` | `oidc`)
- Local dev uses `.env.local` consumed by both backend and `docker-compose.yml`.

### Security In Scope (v1)
- Pre-publish secret scan on uploaded bundles (gitleaks-style)
- Signed-URL downloads from Blob (no app-tier proxy)
- Audit log is append-only (no update/delete on `audit` container)
- Rate limit counters in Redis (sliding window)
- Manager review is the gate for any bundle going public

### Security Out of Scope (v1)
- Bundle sandboxing / execution
- Per-org isolation
- Advanced abuse detection / WAF tuning
- Production SSO hardening beyond Entra ID baseline

### Deployment Considerations
- Single region for v1 (99% availability target). Multi-region is a post-MVP concern.
- Cosmos continuous backup enabled.
- Blob snapshots taken before every curator pass.
- Bicep templates land in `infra/` and are deployed via GitHub Actions.

---

## 10. API Specification

All endpoints return JSON, paginate cursor-style, and include rate-limit headers (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`).

### Public Catalog (auth: API key for agents, OIDC for humans)

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/v1/skills` | List approved skills. Query: `category`, `tag`, `status`, `cursor`, `limit` |
| `GET`  | `/v1/skills/{id}` | Metadata for one skill (latest approved version) |
| `GET`  | `/v1/skills/{id}/versions` | Version history |
| `GET`  | `/v1/skills/{id}/download` | Returns a one-minute signed URL to bundle tar.gz |
| `GET`  | `/v1/skills/{id}/download_url` | SPA helper: returns the one-minute signed URL and expiry so the Get Skill prompt can include it |
| `POST` | `/v1/skills/{id}/usage` | Agent reports load/use event |

### Contributor / Manager (auth: OIDC)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/uploads` | Upload SKILL.md or bundle. Returns pending skill doc |
| `GET`  | `/v1/me/submissions` | List caller's submissions |
| `GET`  | `/v1/admin/queue` | Manager: pending review queue |
| `POST` | `/v1/admin/skills/{id}/approve` | Manager: approve (triggers publish) |
| `POST` | `/v1/admin/skills/{id}/reject` | Manager: reject (reason required) |
| `PATCH`| `/v1/admin/skills/{id}/classification` | Manager: override classifier output |
| `POST` | `/v1/admin/skills/{id}/classify` | Admin: requeue classifier for stuck or legacy unclassified skills, including approved backfill |
| `POST` | `/v1/admin/skills/{id}/defender-rescan` | Admin: clear old Defender result and requeue a scan |
| `POST` | `/v1/admin/skills/{id}/defender-override` | Admin: override a flagged Defender finding with justification |
| `POST` | `/v1/admin/skills/{id}/quarantine` | Admin: move a flagged malicious skill to quarantine |

### Admin (auth: OIDC, admin role)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/admin/curator/run` | Run curator (supports `?dry_run=true`) |
| `POST` | `/v1/admin/curator/pause` | Pause curator schedule |
| `POST` | `/v1/admin/curator/rollback` | Roll back to a snapshot |
| `POST` | `/v1/admin/skills/{id}/pin` | Pin a skill (immune to curator) |
| `POST` | `/v1/admin/skills/{id}/unpin` | Unpin |
| `POST` | `/v1/admin/skills/{id}/restore` | Restore an archived skill |
| `GET`  | `/v1/admin/audit` | Query audit log |

### Example: Upload Response

```json
{
  "skill_id": "github-pr-workflow",
  "version": "1.0.0",
  "status": "pending",
  "uploaded_at": "2026-05-16T12:34:56Z",
  "classifier_status": "queued"
}
```

### Example: Usage Event Payload

```json
{
  "loader_id": "hermes-runtime-42",
  "context": { "session_id": "abc123", "platform": "macos" }
}
```

### Data Model — Cosmos Containers

#### Container: `skills` (partition key: `/skill_id`)
```json
{
  "id": "uuid",
  "skill_id": "stable-skill-id",
  "version": "1.0.0",
  "name": "github-pr-workflow",
  "status": "pending|classified|approved|rejected|stale|archived",
  "uploader": "user@org",
  "uploaded_at": "iso8601",
  "approved_at": "iso8601|null",
  "approver": "user@org|null",
  "rejection_reason": "string|null",
  "classification": {
    "category": "github",
    "tags": ["pr", "workflow", "git"],
    "quality_score": 87,
    "summary": "...",
    "duplicate_candidates": ["skill-id-1", "skill-id-2"],
    "classifier_version": "v1",
    "classified_at": "iso8601"
  },
  "bundle": {
    "blob_url": "https://...",
    "checksum_sha256": "...",
    "size_bytes": 12345,
    "file_count": 4
  },
  "usage": {
    "load_count": 0,
    "last_loaded_at": "iso8601|null",
    "loaders_30d": 0
  },
  "pinned": false,
  "pinned_by": "user@org|null"
}
```

#### Container: `audit` (partition key: `/skill_id`)
```json
{
  "id": "uuid",
  "skill_id": "stable-skill-id",
  "action": "upload|classify|approve|reject|publish|archive|pin|unpin|restore|rollback",
  "actor": "user@org|system:classifier|system:curator",
  "at": "iso8601",
  "before": { "...": "..." },
  "after": { "...": "..." },
  "metadata": { "reason": "...", "...": "..." }
}
```

#### Container: `usage_events` (partition key: `/skill_id`, TTL: 90 days)
```json
{
  "id": "uuid",
  "skill_id": "...",
  "version": "1.0.0",
  "loader_id": "agent-runtime-id",
  "at": "iso8601",
  "context": { "session_id": "...", "platform": "..." }
}
```

---

## 11. Success Criteria

### MVP Success Definition
The MVP is successful when a contributor can upload a SKILL.md on a local dev stack, see it auto-classified within 60 seconds, have a manager approve it, and have an agent runtime list and download it via the public API — all without touching Azure.

### Functional Requirements
- ✅ Upload → pending doc in Cosmos within 2s for files <1MB
- ✅ Classifier runs async and writes results back to Cosmos within 60s p95
- ✅ Manager review queue shows pending skills with classifier output
- ✅ Approve creates immutable tar.gz in Blob with checksum
- ✅ Public catalog API returns approved skills with <300ms p95 latency
- ✅ Usage events accumulate per-skill and TTL after 90 days
- ✅ Curator dry-run produces report without mutations
- ✅ Snapshot + rollback round-trip works
- ✅ Pinned skills are skipped by every curator transition
- ✅ Audit log captures every state transition

### Quality Indicators
- Time-from-upload to-approval: **<48h p50**
- Classifier accuracy: **manager-override rate <30%**
- Zero accidental deletions (hard invariant — measured, not goaled)
- Cache hit rate on `/v1/skills` list endpoint: >80% steady-state
- Janitor re-queue rate (lost classifier jobs): <1% of uploads

### User Experience Goals
- Contributors get same-day feedback on submissions
- Managers can clear a 20-skill queue in under 30 minutes
- Agent runtimes can integrate with the catalog API in a single afternoon

---

## 12. Implementation Phases

### Phase M0 — POC (target: 2 weeks)
**Goal:** Prove the end-to-end flow on local emulators with zero Azure spend.

**Deliverables:**
- ✅ Repo scaffolded; ARCHITECTURE.md + this PRD committed
- ✅ docker-compose: Cosmos emulator + Azurite + redis:7
- ✅ Backend: upload → Cosmos pending → classifier worker (BLPOP) → status updates
- ✅ Frontend: upload form, my-submissions view, manager review queue
- ✅ Approve flow: writes tar.gz to Azurite, flips Cosmos status
- ✅ Public list/get/download API
- ✅ Basic audit log on every transition

**Validation:** End-to-end happy path (upload → classify → approve → list → download) demoable locally; no Azure resources provisioned.

### Phase M1 — Azure deployment + auth (target: +2 weeks)
**Goal:** Get the POC running in Azure with real authentication.

**Deliverables:**
- ✅ Bicep templates for Cosmos, Blob, Redis, Functions, App Service
- ✅ Entra ID OIDC integration (replaces header stub)
- ✅ API key issuance + rotation for agent runtimes
- ✅ GitHub Actions CI/CD (build, test, deploy)
- ✅ App Insights wiring (logs + traces)

**Validation:** A real user authenticates via Entra ID, uploads a skill in the deployed environment, and a real agent runtime downloads it using an API key.

### Phase M2 — Curator (target: +2 weeks)
**Goal:** Ship the lifecycle maintenance layer.

**Deliverables:**
- ✅ Usage tracking pipeline (POST /usage → counters → 30d rolling window)
- ✅ Deterministic stale (30d) / archive (90d) transitions on schedule
- ✅ Snapshot-before-pass + rollback CLI
- ✅ Pinning + unpinning
- ✅ Admin endpoints: `pause`, `resume`, `run --dry-run`, `run`, `rollback`, `restore`
- ✅ Janitor sweep for lost classifier queue messages

**Validation:** Dry-run report matches real-run diff; rollback restores prior state byte-for-byte; pinned skills survive a full curator cycle untouched.

### Phase M3 — Curator LLM review (target: +1 week)
**Goal:** Add the consolidation/drift suggestions layer.

**Deliverables:**
- ✅ Aux-model review pass on active skills
- ✅ Consolidation suggestions surfaced in manager UI as actionable tickets
- ✅ Per-run skill cap (default 50, manager-configurable)

**Validation:** Manager receives 3+ actionable suggestions per run on a seeded duplicate corpus; suggestions are reviewable, dismissible, or actionable.

### Phase M4 — Hardening (ongoing)
**Goal:** Production-readiness.

**Deliverables:**
- ✅ Rate limiting on all public endpoints
- ✅ Pre-publish secret scan (gitleaks-style) integrated into publish job
- ✅ Observability runbooks
- ✅ Backup + restore drills
- ✅ Capacity planning doc

**Validation:** Synthetic load test passes SLOs; runbook dry-run completes end-to-end.

### Phase M5 — Defender, quarantine, notifier (target: +1 week)
**Goal:** Add an LLM-based malicious-bundle scanner, an admin-controlled
quarantine carve-out, and a notification fan-out so the review and
lifecycle flows actually reach humans. Full plan:
`.agents/plans/m5-defender-quarantine-notifier.md`.

**Deliverables:**
- ✅ `quarantine/` Blob container — the ONE delete-after-N-days exception
  in the system; everything else stays archive-only (AGENTS.md §5).
- ✅ Defender worker (`backend/workers/defender.py`) — BLPOPs
  `queue:defender`, runs an LLM scan against Microsoft Foundry, writes
  `defender_status`/`defender_severity`/`defender_report` to Cosmos.
  Fake provider keeps unit + integration tests cheap.
- ✅ Quarantine service + admin endpoint
  (`POST /v1/admin/skills/{id}/quarantine`) with mandatory
  justification; bytes copy to `quarantine/`, status flips to
  `quarantined`, audit row recorded with `source=admin_manual`.
- ✅ Quarantine janitor (`backend/services/quarantine_janitor.py`) —
  guarded `delete_blob` allowlist in the AST gate; deletes bundles
  past `quarantine_expires_at`, never the Cosmos doc.
- ✅ Defender admin UI — report rendering, override-with-justification,
  quarantine button surfaced in the review queue. Approval is blocked until
  Defender completes; admins can rescan Defender from skill detail pages and
  can see Defender reports inline in the review queue.
- ✅ Notifier worker (`backend/workers/notifier.py`) — fan-out via ACS
  email + Microsoft Graph admin-group lookup, with Redis dedupe locks
  (`notif:sent:{idempotency_key}`) and Jinja-style templates per
  event type. Fake ACS + fake Graph clients keep CI offline.
- ✅ Producers wired across upload, classifier, defender, publish,
  reject, quarantine, override, curator — every M5 event type emits
  at the corresponding callsite.
- ✅ Curator schedule admin UI + reconcile-to-CronJob worker —
  Cosmos-backed schedule doc (`system_state/curator_schedule`) with a
  GET/PUT admin pair; a reconciler worker annotates the K8s CronJob.

**Validation:** Local emulator stack runs the M5-8 e2e tests
(`backend/tests/e2e/test_m5_full_flow.py`) green; admin can quarantine
a flagged skill end-to-end and receive a notifier event; curator
schedule changes propagate to the CronJob annotation.

---

## 13. Future Considerations

- **In-browser skill editor.** Edit + diff against latest version, draft state.
- **Multi-tenant / org isolation.** Per-org namespaces, RBAC, quotas.
- **Public marketplace.** External publishing of selected skills.
- **Sandbox testing.** Run a skill against synthetic inputs to validate behavior before approval.
- **Trusted-uploader auto-approve.** Reputation-based bypass for the review queue.
- **Per-skill semantic search.** Vector index over SKILL.md bodies for better discovery.
- **Federation.** Multiple hubs that sync approved catalogs.
- **Skill telemetry dashboards.** Per-skill health, error rates, drift indicators.
- **CDN-fronted downloads** in production for global agent runtimes.

---

## 14. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Classifier mis-categorizes at scale | Medium | Low | Manager can override; classifier output is a suggestion, not authority |
| Curator archives a skill someone needed | Low | Medium | 30/90-day grace; pinning; snapshot + rollback; never auto-deletes |
| Cosmos costs balloon with usage events | Medium | Medium | TTL on `usage_events` (90d); aggregated counters live on the skill doc |
| Manager review becomes a bottleneck | High | Medium | Quality-score sorting, bulk-approve UI, eventual trusted-uploader auto-approve |
| Storage account is a single point of failure | Low | High | GRS replication, snapshots; Cosmos is SoR so Blob is regenerable |
| Skill bundle contains secrets / malicious payloads | Medium | High | Pre-publish scan, manager review gate, never auto-execute |
| Redis outage degrades the app | Medium | Low | Every Redis read path has a Cosmos fallback (rule #2); slower, not broken |
| Lost classifier queue message | Low | Low | Pending doc written to Cosmos before enqueue; janitor sweep re-queues |

---

## 15. Open Questions

These are preserved for Michael to answer before or during M0.

1. **Skill taxonomy** — adopt Hermes categories (devops, mlops, productivity, …) as-is, or design a custom one for the org? *Default: Hermes categories.*
2. **Versioning semantics** — semver enforced, or freeform string? Auto-bump on every approval, or contributor-declared? *Default: semver, auto-bump patch on each approval unless contributor specifies.*
3. **Per-skill ownership** — should only the original uploader (or designated owners) be allowed to submit new versions? *Default: yes, with admin override.*
4. **Duplicate handling** — when classifier flags duplicates, hard-block or warn-only? *Default: warn, manager decides.*
5. **Draft state** — do we need a private/draft state visible only to uploader before submission? *Default: no in v1; save draft client-side.*
6. **Skill checks** — beyond schema validation, any automated checks (referenced tools exist, commands syntactically valid)? *Default: schema only in v1; deeper validation later.*
7. **Curator LLM budget** — cap N skills per review run? *Default: cap at 50 per run, manager-configurable.*

---

## 16. Appendix

### Related Documents
- `.opencode/CONTEXT.md` — full requirements conversation between Michael Liav and Hermes
- `README.md` — short project overview
- `infra/` — Bicep templates (forthcoming, M1)

### Key Dependencies
- [FastAPI](https://fastapi.tiangolo.com/)
- [Next.js 14](https://nextjs.org/)
- [Azure Cosmos DB for NoSQL](https://learn.microsoft.com/azure/cosmos-db/nosql/)
- [Azure Cache for Redis](https://learn.microsoft.com/azure/azure-cache-for-redis/)
- [Azure Blob Storage](https://learn.microsoft.com/azure/storage/blobs/)
- [Azure Functions](https://learn.microsoft.com/azure/azure-functions/)
- [Bicep](https://learn.microsoft.com/azure/azure-resource-manager/bicep/)
- [Azurite (Blob emulator)](https://learn.microsoft.com/azure/storage/common/storage-use-azurite)
- [Cosmos DB Emulator](https://learn.microsoft.com/azure/cosmos-db/local-emulator)
