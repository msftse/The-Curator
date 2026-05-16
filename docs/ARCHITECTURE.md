# Agentic Skill Hub — Architecture Map

**Document Version:** 2.0
**Date:** 2026-05-16
**Purpose:** Comprehensive architectural reference for new contributors, code reviewers, and operators. This document supersedes v1.0 and reflects the current state of the codebase, including the Azure passwordless deployment path, Foundry-backed LLM curator review (M3), and the v1.0-era foundations (M0–M2).

All file references use `path:line` form (e.g. `backend/core/config.py:50`).

---

## 1. Codebase Overview

Agentic Skill Hub is an internal web platform for submitting, reviewing, publishing, and maintaining reusable agent skills (`SKILL.md` bundles compatible with Hermes Agent and similar frameworks).

End-to-end flow:

```
upload  →  classify (auto, async)  →  manager review  →  publish (immutable tar.gz)
        →  public catalog API  →  consumer (agent runtime) downloads + reports usage
        →  curator (deterministic lifecycle + LLM content review, manager-approved)
```

Personas: **Contributor**, **Manager**, **Admin** (humans, OIDC + API keys), **Consumer** (agent runtime, API keys).

Milestones:
- **M0** — POC, local emulator round-trip. Done.
- **M1** — OIDC auth (Entra ID), API keys, telemetry. Done.
- **M2** — Curator lifecycle (stale/archive + snapshot/rollback + janitor). Done.
- **M3** — Curator LLM review pass (Azure AI Foundry, manager-approved proposals). Done.
- **M4** — Hardening, multi-master Redlock, additional providers. Future.

---

## 2. Architectural Pattern

**Layered + event-driven hybrid.**

| Layer | Responsibility | Locations |
|-------|----------------|-----------|
| Presentation | Next.js 14 (Server Components + Tailwind) | `frontend/app/`, `frontend/components/` |
| API | FastAPI, async end-to-end, DI via `Depends` | `backend/api/`, `backend/app.py` |
| Domain services | Pure-ish orchestration of Cosmos/Redis/Blob/LLM | `backend/services/` |
| Storage clients | Async Cosmos / Redis / Blob / Foundry, identity-aware | `backend/core/` |
| Workers | Long-running asyncio processes (classifier, curator) | `backend/workers/` |
| Infra | Azure Bicep modules (Cosmos / Redis / Storage / App Service / SWA / Key Vault / AI / RBAC) | `infra/` |

### The five non-negotiable principles (AGENTS.md §3–§5)

1. **Cosmos-first writes.** Every durable write hits Cosmos first; Redis is invalidated *after* success.
2. **Cache misses are normal.** Every Redis read has a Cosmos fallback. Redis down ⇒ slower, never broken.
3. **TTL everything.** No infinite-lived Redis keys.
4. **Classifier queue exception** mitigated by AOF + Cosmos-pending-first + janitor re-queue sweep.
5. **Never-delete invariant.** Curator only archives. `published/` blobs and `skills` Cosmos rows are immune to deletion. Pinned skills are immune to *every* auto-transition. Statically enforced by an AST gate (`backend/tests/unit/test_never_delete_invariant.py`).

---

## 3. Technology Stack

| Layer | Component | Notes |
|-------|-----------|-------|
| Frontend | Next.js 14, TypeScript strict, Tailwind | Server Components by default; `"use client"` only when needed |
| Backend | FastAPI on Python 3.12, fully async | Pydantic v2 models; `uv` for dependency mgmt |
| System of record | Azure Cosmos DB for NoSQL | PK `/skill_id` on skill+audit+usage containers |
| Cache + queue + locks | Azure Cache for Redis 7 (AOF on queue) | Entra ID or access key auth |
| Object storage | Azure Blob Storage | User-delegation SAS for downloads in identity mode |
| LLM (M3) | Azure AI Foundry (`azure-ai-inference`) | Managed Identity or API key |
| AuthN/Z | Entra ID OIDC + Microsoft Authentication Library (MSAL) on FE; API keys for agents | Stub/fake-OIDC modes for local dev |
| Telemetry | OpenTelemetry → Azure Monitor / App Insights | No-op when connection string is unset |
| Local dev | docker-compose: Cosmos emulator + Azurite + Redis 7 | Zero Azure spend |
| Infra-as-code | Azure Bicep | `infra/main.bicep` + `infra/modules/*` + per-env `parameters/` |
| CI/CD | GitHub Actions | Lint + test + bicep what-if |

Pinned constraints worth knowing about:

- `aiohttp>=3.9,<3.10` in `pyproject.toml`. azure-cosmos 4.15/4.16b leaks the `enable_cross_partition_query` kwarg into aiohttp ≥ 3.10, which rejects unknown kwargs. We hold aiohttp on 3.9.x until azure-cosmos ships a fix.
- `azure-cosmos>=4.7,<4.16` for the same reason.

---

## 4. Directory Structure

```
agentic-skill-hub/
├── backend/
│   ├── app.py                       # FastAPI factory + lifespan (boot order)
│   ├── api/                         # Thin route modules per area
│   │   ├── uploads.py               # POST /v1/uploads, GET /v1/me/submissions
│   │   ├── skills.py                # GET /v1/skills, /skills/{id}, /download, /versions; POST usage
│   │   ├── admin.py                 # /v1/admin/queue, approve, reject, reclassify
│   │   ├── curator.py               # /v1/admin/curator/* (incl. M3 /review*)
│   │   └── api_keys.py              # /v1/admin/api-keys CRUD
│   ├── core/
│   │   ├── config.py                # Pydantic settings, .env.local
│   │   ├── cosmos.py                # Async client + container bootstrap
│   │   ├── redis.py                 # Async client + Entra credential provider + lock
│   │   ├── blob.py                  # Async client + user-delegation SAS
│   │   ├── deps.py                  # FastAPI DI bindings (incl. LLM provider singleton)
│   │   ├── errors.py                # Stable domain error codes + handlers
│   │   ├── logging.py               # JSON logger + contextvars (`bind(...)`)
│   │   ├── telemetry.py             # OpenTelemetry / App Insights wiring
│   │   └── auth/                    # IdentityProvider abstraction
│   │       ├── deps.py              # get_current_user, get_principal, require_role/scope
│   │       ├── api_keys.py          # API-key hashing, lookup, cache
│   │       ├── models.py            # User, ServiceAccount, Principal, Role, Scope
│   │       └── providers/           # stub, fake, oidc, saml
│   ├── services/                    # Domain logic (see §6)
│   │   ├── llm/                     # Foundry + Fake providers
│   │   └── ... (see §6 for full list)
│   ├── workers/
│   │   ├── classifier.py            # BLPOP queue:classifier loop
│   │   └── curator_scheduler.py     # M2 deterministic + M3 review cadence
│   ├── models/                      # Pydantic request/response + Cosmos doc schemas
│   └── tests/                       # unit/ + integration/ (see §12)
├── frontend/
│   ├── app/                         # upload, my-submissions, admin/queue, admin/curator/*
│   ├── components/                  # incl. components/curator/* (M2+M3 UI)
│   └── lib/{api,auth,hooks}/        # typed API client, MSAL provider, hooks
├── infra/                           # Bicep — see §14
├── scripts/                         # setup-entra.sh, seed_skills.py, install_emulator_cert.sh, …
├── docker-compose.yml               # Cosmos emulator + Azurite + Redis 7
├── docs/PRD.md                      # Product reqs v0.2
├── docs/ARCHITECTURE.md             # THIS FILE
└── AGENTS.md                        # Non-negotiable conventions
```

---

## 5. Entry Points

| Process | Command | Wiring file |
|---------|---------|-------------|
| API | `uv run uvicorn backend.app:create_app --factory --reload` | `backend/app.py:86` |
| Classifier worker | `python -m backend.workers.classifier` | `backend/workers/classifier.py:149` |
| Curator scheduler | `python -m backend.workers.curator_scheduler` | `backend/workers/curator_scheduler.py:158` |
| Frontend | `pnpm --filter frontend dev` | `frontend/app/layout.tsx` |
| Local stack | `docker compose up -d` | `docker-compose.yml` |
| Seed data | `python scripts/seed_skills.py` | `scripts/seed_skills.py` |

### API lifespan boot order (`backend/app.py:42`)

1. Configure JSON logging.
2. `settings.enforce_production_safety()` — refuse to start prod with `AUTH_MODE=stub` unless `LOCAL_DEV=1` (`backend/core/config.py:212`).
3. Configure OpenTelemetry (no-op if `APPLICATIONINSIGHTS_CONNECTION_STRING` is empty).
4. Build async Cosmos client (`backend/core/cosmos.py:34`).
5. `ensure_containers()` — gracefully degrades to data-plane-only on 401/403.
6. Build Redis client; `ping()` is best-effort (boot continues if Redis is down).
7. Build Blob client; `ensure_containers()` for `published`, `archive`, `snapshots`, and the curator reports container.
8. Attach `settings`, clients, `api_keys_container`, and the resolved `identity_provider` to `app.state`.
9. On shutdown, close Redis → Blob → Cosmos in order. `aiohttp` leaks a single "Unclosed client session" warning from Cosmos's session — cosmetic only.

`/healthz` probes Cosmos `.read()`, Redis `ping()`, Blob `get_service_properties()` and returns per-backend status.

---

## 6. Services (Domain Layer)

Every state transition (`upload`, `classify`, `approve`, `reject`, `publish`, `archive`, `pin`, `unpin`, `restore`, `rollback`, `review_*`) writes an immutable row to the `audit` Cosmos container via `backend/services/audit.py:record`. No transition without an audit row.

| Service | Responsibility | Key file |
|---------|----------------|----------|
| `upload` | Validate bundle, persist pending Cosmos doc, RPUSH classifier queue | `services/upload.py:38` |
| `skill_bundle` | Parse / build deterministic tar.gz; SKILL.md frontmatter | `services/skill_bundle.py` |
| `classification` / `classifier_stub` | Classifier interface + naïve stub | `services/classification.py`, `services/classifier_stub.py` |
| `publish` | Lock → Cosmos flip → audit → cache invalidate; idempotent | `services/publish.py:36` |
| `catalog` | Public catalog list + single + versions, cache-first with Cosmos fallback | `services/catalog.py` |
| `usage` | Aggregate counters on `SkillDoc` + raw `usage_events` (90d TTL) | `services/usage.py` |
| `audit` | Append-only row writer; no updates, no deletes | `services/audit.py` |
| `cosmos_helpers` | `replace_with_etag_retry` (optimistic concurrency) | `services/cosmos_helpers.py` |
| `api_keys` | Issue / list / revoke API keys | `services/api_keys.py` |
| **Curator (M2)** | | |
| `curator` | Deterministic planner + executor (stale/archive transitions) | `services/curator.py` |
| `curator_state` | Pause flag in `system_state` container | `services/curator_state.py` |
| `snapshot` | Full tar.gz of `published/` to `snapshots/{utc-iso}/`; rotate retention | `services/snapshot.py` |
| `curator_rollback` | Byte-for-byte restore from a snapshot | `services/curator_rollback.py:65` |
| `curator_report` | Markdown report per run | `services/curator_report.py` |
| `janitor` | Re-queue Cosmos `classifier_status=queued` docs older than threshold | `services/janitor.py:28` |
| **Curator review (M3)** | | |
| `curator_review` | Drift + consolidation review pass against Foundry; emits proposals | `services/curator_review.py` |
| `curator_review_prompts` | Versioned prompt templates (drift + consolidation) | `services/curator_review_prompts.py` |
| `curator_review_similarity` | TF-IDF cosine pre-filter for consolidation candidates | `services/curator_review_similarity.py` |
| `curator_review_apply` | Manager approval pipeline — apply patch / merge / reject | `services/curator_review_apply.py:127,167,282` |
| `curator_review_report` | Markdown report per review run | `services/curator_review_report.py` |
| **LLM** | | |
| `llm/provider` | `LLMProvider` ABC + `LLMResult` | `services/llm/provider.py` |
| `llm/foundry` | Azure AI Foundry impl (Key or DefaultAzureCredential) | `services/llm/foundry.py` |
| `llm/fake` | Canned responses for unit tests | `services/llm/fake.py` |

---

## 7. API Surface

| Router | Prefix | File |
|--------|--------|------|
| Uploads | `/v1` | `backend/api/uploads.py:21` |
| Catalog | `/v1/skills` | `backend/api/skills.py:30` |
| Admin (review queue) | `/v1/admin` | `backend/api/admin.py:29` |
| Admin (curator) | `/v1/admin/curator` | `backend/api/curator.py:84` |
| Admin (API keys) | `/v1/admin/api-keys` | `backend/api/api_keys.py:23` |

### Endpoints (selected)

**Uploads**
- `POST /v1/uploads` — accept `.md` or tar(.gz) bundle. Returns pending `SkillDoc`.
- `GET /v1/me/submissions` — caller's submissions.

**Catalog (public, scope-gated)**
- `GET /v1/skills` — cached 60 s; Cosmos fallback.
- `GET /v1/skills/{id}` — cached 5 min.
- `GET /v1/skills/{id}/download` — returns a 15-minute SAS URL (user-delegation in identity mode).
- `GET /v1/skills/{id}/versions`.
- `POST /v1/skills/{id}/usage` — records a `usage_events` row + bumps counters.

**Admin review**
- `GET /v1/admin/queue` — `pending`/`classified` skills.
- `POST /v1/admin/skills/{id}/approve` — locks → publishes → audits.
- `POST /v1/admin/skills/{id}/reject`.
- `PATCH /v1/admin/skills/{id}/classification`.
- `POST /v1/admin/skills/{id}/archive` — admin-issued manual archive of an
  approved skill (soft delete). Body `{ "reason": "..." }`. Reuses the
  curator's archive primitives: bundle copied to `archive/`, status flips
  to `archived`, audit row written with `source=admin_manual`. Refuses
  pinned (`SKILL_PINNED`) and non-approved skills
  (`INVALID_STATUS_TRANSITION`). Recoverable via
  `POST /v1/admin/curator/restore/{id}`. **Never deletes.**

**Admin curator (M2)**
- `POST /v1/admin/curator/{pause,resume}`.
- `POST /v1/admin/curator/run` — dry-run or real; returns `CuratorRunRecord`.
- `POST /v1/admin/curator/rollback` — restore from a snapshot.
- `POST /v1/admin/curator/restore/{skill_id}` — single-skill restore.
- `POST /v1/admin/curator/{pin,unpin}/{skill_id}`.
- `GET /v1/admin/curator/{status,snapshots,runs,runs/{id}/report}`.
- `POST /v1/admin/curator/janitor` — sweep classifier queue.

**Admin curator review (M3)**
- `POST /v1/admin/curator/review` — on-demand review pass (drift + consolidation). Body: `{ "dry_run": bool }`.
- `GET /v1/admin/curator/reviews` — list proposals (filterable).
- `GET /v1/admin/curator/reviews/{proposal_id}`.
- `POST /v1/admin/curator/reviews/{proposal_id}/{approve,reject}`.

**Admin API keys (M1)**
- `POST /v1/admin/api-keys`, `GET …`, `DELETE …/{key_id}` — manage agent credentials. The plaintext token is shown exactly once at issue time.

### Error model

All domain errors inherit from `DomainError` and serialize as `{ "error_code": "…", "message": "…", "metadata": {…} }`. Codes are stable contracts (see `backend/core/errors.py`):

```
SKILL_NOT_FOUND, INVALID_BUNDLE, BUNDLE_TOO_LARGE, ALREADY_PUBLISHED, LOCK_UNAVAILABLE,
FORBIDDEN, UNAUTHORIZED, INVALID_TOKEN, REVOKED_API_KEY, MISSING_SCOPE,
SKILL_PINNED, INVALID_STATUS_TRANSITION,
CURATOR_PAUSED, SNAPSHOT_NOT_FOUND, RESTORE_FAILED, CURATOR_RUN_REPORT_NOT_FOUND,
REVIEW_PROPOSAL_NOT_FOUND, REVIEW_PROPOSAL_STALE, REVIEW_PROPOSAL_NOT_PENDING,
LLM_PROVIDER_ERROR
```

---

## 8. AuthN / AuthZ

Two principal types, one resolver: `Principal = User | ServiceAccount` (`backend/core/auth/models.py`).

### Humans — `IdentityProvider` (`backend/core/auth/providers/`)

| Mode | Provider | Use |
|------|----------|-----|
| `stub` | `StubProvider` | Local dev — reads `X-User-Email` header; role allowlists in `MANAGER_EMAILS`/`ADMIN_EMAILS`. |
| `fake_oidc` | `FakeOIDCProvider` | Local Entra exercise without a real tenant. |
| `oidc` | `OIDCProvider` | Production — Entra ID, JWKS-cached, groups → roles via `ENTRA_GROUP_ID_ADMIN`. |
| `saml` | `SAMLProvider` | Reserved for federated tenants. |

Selected once at lifespan startup (`backend/core/auth/__init__.select_provider`) and stored on `app.state.identity_provider`. `enforce_production_safety()` refuses `stub`/`fake_oidc` outside `LOCAL_DEV=1`.

### Machines — API keys (`backend/core/auth/api_keys.py`)

- Token format: `sh_live_<random>`. Stored as `(prefix, sha256_with_pepper(token))` — never plaintext.
- `resolve_api_key` does a Cosmos lookup keyed by prefix, validates with the peppered hash, checks revocation, caches the resolved `ServiceAccount` in Redis (TTL `APIKEY_CACHE_TTL_SECONDS`, default 60 s).
- `get_principal` dispatches on `Authorization: Bearer sh_live_…` → API key; otherwise → user provider (`backend/core/auth/deps.py:49`).

### Role + scope checks

- `require_role("admin"|"manager")` — humans only; on admin success records one `admin_session_start` audit row per (oid, 24 h) using `SETNX` (rule #4: ephemeral coordination, Cosmos has the durable record).
- `require_scope("catalog:read"|"usage:write"|…)` — `ServiceAccount` must carry the scope; `User` implicitly satisfies all scopes.

### Frontend auth

`frontend/lib/auth/AuthProvider.tsx` + `msal.ts` wire MSAL into Next; the typed API client (`frontend/lib/api/client.ts`) acquires Entra access tokens and attaches them as `Authorization: Bearer …`. In stub mode it sends `X-User-Email` instead.

---

## 9. Storage Split

Non-negotiable; full rationale in AGENTS.md §3.

### 9.1 Cosmos DB — system of record

| Container | PK | TTL | Purpose | File |
|-----------|----|----|---------|------|
| `skills` | `/skill_id` | none | Skill metadata + status + classification + bundle ref | `backend/core/cosmos.py:25` |
| `audit` | `/skill_id` | none | Append-only audit rows; never updated, never deleted | `backend/services/audit.py` |
| `usage_events` | `/skill_id` | 7,776,000 s (90 d) | Raw usage telemetry; aggregated counters live on `SkillDoc` | `backend/core/cosmos.py:27` |
| `api_keys` | `/key_id` | none | Peppered hashes + scopes for service accounts | `backend/core/auth/api_keys.py` |
| `system_state` | `/key` | none | Curator pause flag, etc. | `backend/services/curator_state.py` |
| `review_proposals` | `/run_id` | none | M3 LLM verdicts; PK by run_id for cheap per-run listing | `backend/models/review.py` |

`ensure_containers()` (`backend/core/cosmos.py:59`) tries `create_*_if_not_exists` first and quietly falls through on 401/403 when the identity has data-plane-only RBAC (production posture: containers are pre-provisioned via Bicep, the app must not require control-plane rights at boot).

Optimistic concurrency: `services/cosmos_helpers.replace_with_etag_retry` reads `_etag`, retries on 412 with bounded attempts.

### 9.2 Redis — cache + ephemeral coordination

Key namespaces (`backend/core/redis.py:132`):

- `cache:skills:list:v1` — public list response, TTL 60 s.
- `cache:skills:item:{skill_id}` — single skill, TTL 300 s.
- `queue:classifier` — RPUSH/BLPOP queue. **AOF enabled**.
- `lock:publish:{skill_id}` — `SET NX EX 30s` around `publish()`.
- `lock:curator:run` — held for the entire curator pass.
- `curator:paused` — boolean.
- `apikey:{prefix}` — 60 s cache of resolved `ServiceAccount`.
- `admin_seen:{oid|email}` — 24 h SETNX guard around the `admin_session_start` audit row.

Lock idiom: `redis_lock(redis, key, ttl)` uses `SET NX EX` + a Lua compare-and-delete release (`backend/core/redis.py:159`). Good enough for a single Redis instance; Redlock is M4.

### 9.3 Blob — immutable artifact bytes

Containers (`backend/core/blob.py:61`):

- `published/{skill_id}/{version}/bundle.tar.gz` — published bundles. Source of bytes.
- `archive/{skill_id}/{version}/bundle.tar.gz` — copies created when curator archives a skill. Originals in `published/` are intentionally left for defense-in-depth (catalog filters by `status='approved'`).
- `snapshots/{utc-iso-compact}/skills.tar.gz` — pre-pass tar of all `published/` blobs + manifest JSON. Default retention 5 (`CURATOR_SNAPSHOT_RETENTION`).
- `curator/runs/…` and `curator/reviews/…` — Markdown reports.

Downloads NEVER proxy bytes through the API tier. `signed_download_url` produces a 15-minute SAS:

- **Identity mode** (`BLOB_ACCOUNT_URL` set): user-delegation SAS, signed via AAD; no account key required (`backend/core/blob.py:120`).
- **Connection-string mode** (Azurite / local dev): account-key SAS.

---

## 10. Passwordless Auth & Identity Mode

Each storage client supports two auth modes, selected by environment:

### 10.1 Cosmos DB (`backend/core/cosmos.py:50`)

- `COSMOS_KEY` non-empty ⇒ master-key auth (emulator default).
- `COSMOS_KEY` empty ⇒ `DefaultAzureCredential`. The calling principal must hold the **`Cosmos DB Built-in Data Contributor`** data-plane role (control-plane RBAC alone is insufficient — `az cosmosdb sql role assignment create …`).

`ensure_containers()` swallows 401/403 from control-plane creates so a strictly data-plane identity can still boot.

### 10.2 Blob Storage (`backend/core/blob.py:46`)

- `BLOB_CONNECTION_STRING` set, `BLOB_ACCOUNT_URL` empty ⇒ connection-string auth.
- `BLOB_ACCOUNT_URL` set ⇒ `DefaultAzureCredential`. Required role: **`Storage Blob Data Owner`** (read/write/SAS-issue). User-delegation SAS is generated via `get_user_delegation_key`.

### 10.3 Redis (`backend/core/redis.py:39`)

- `REDIS_URL` only ⇒ URL credential (Azurite locally; `rediss://:KEY@…` for Azure access keys).
- `REDIS_USE_ENTRA=true` ⇒ `_EntraTokenCredentialProvider`, an async `redis.credentials.CredentialProvider` that:
  - mints a token against `https://redis.azure.com/.default` via `DefaultAzureCredential`,
  - caches it and refreshes 2 minutes before expiry,
  - returns `(REDIS_ENTRA_USERNAME = <object_id>, <token>)` on every `get_credentials_async()` redis-py call.

The principal must have a Redis **`Data Owner`** or **`Data Contributor`** access policy and `aadEnabled=true` on the cache. The username is the AAD **object id** (oid) of the principal — not its email.

### 10.4 Azure AI Foundry (`backend/services/llm/foundry.py:46`)

- `AZURE_AI_FOUNDRY_API_KEY` set ⇒ `AzureKeyCredential` (local dev).
- Otherwise ⇒ `DefaultAzureCredential`. Required role: **`Cognitive Services User`** on the Foundry resource.
- Endpoint must include the `/models` suffix for inference (`https://<name>.services.ai.azure.com/models`).

`DefaultAzureCredential` resolution order is consistent everywhere: env vars → Workload/Managed Identity → `az login` → Azure Developer CLI → Azure PowerShell → interactive browser. Local dev path is `az login`.

---

## 11. End-to-End Data Flows

### 11.1 Upload → Classify → Publish

```
Contributor                           API                              Worker
  │  POST /v1/uploads (file)            │                                  │
  │ ─────────────────────────────────▶  │ enforce_size + parse SKILL.md    │
  │                                     │ build deterministic tar          │
  │                                     │ 1. skills.create_item(...)       │
  │                                     │ 2. audit.record(upload)          │
  │                                     │ 3. RPUSH queue:classifier        │
  │   201 SkillDoc(status=pending)      │                                  │
  │ ◀─────────────────────────────────  │                                  │
  │                                     │            BLPOP ──────────────▶ │ classify
  │                                     │                                  │ classifier_stub|llm
  │                                     │  replace_item(status=classified) │
  │                                     │  audit.record(classify)          │
  │                                     │  DEL cache:skills:item:{id}      │
Manager                                  │                                  │
  │ POST /v1/admin/skills/{id}/approve   │ redis_lock(lock:publish:{id})    │
  │ ──────────────────────────────────▶  │ build_tar + put_published        │
  │                                     │ replace_item(status=approved)    │
  │                                     │ audit.record(approve, publish)   │
  │                                     │ DEL cache:skills:{list,item}     │
```

Files: `services/upload.py`, `workers/classifier.py`, `services/publish.py`.

### 11.2 Download (signed)

`GET /v1/skills/{id}/download` → `services/catalog.get_one` (cache-first) → `core/blob.signed_download_url` → 302 to a 15-minute SAS. App tier never sees bytes.

### 11.3 Usage

`POST /v1/skills/{id}/usage` → write `usage_events` row (90-day TTL via container default) → bump `load_count` / `last_loaded_at` on the `SkillDoc` with etag retry.

### 11.4 Curator deterministic pass (M2)

```
acquire lock:curator:run (1800 s TTL)
  if curator:paused -> raise CuratorPaused
  snapshot_published(...)                              # tar all of published/
  rotate_retention(...)                                # keep 5 newest
  plan_transitions(docs, now, stale=30d, archive=90d)  # pure
  for each transition:
    re-read doc; skip if pinned changed
    if archive:  copy blob published/ -> archive/ (leave source)
    replace_with_etag_retry(skills, doc with new status)
    audit.record(archive|stale, ...)
    DEL cache:skills:{list,item}
  curator_report.write(...)                            # Markdown to blob curator/runs/
release lock
```

`plan_transitions` is pure — same input + same `now` ⇒ same output, so dry-runs are exactly comparable to real runs. Pinned skills are filtered out by the planner and re-checked by the executor.

### 11.5 Curator LLM review (M3)

```
acquire lock:curator:run                         # shared with deterministic pass
  if curator:paused -> abort with reason="paused"
  candidates: status=approved AND pinned=false AND uploader STARTSWITH "agent:"
              ORDER BY load_count DESC, LIMIT 50
  for each candidate:
    read SKILL.md from published/{id}/{ver}/bundle.tar.gz (Blob is source of truth)
    LLMProvider.complete(drift_prompt) -> ReviewProposal(kind=patch|keep)
    persist to review_proposals (Cosmos-first)
    if total_tokens > cap: abort with reason="cost_cap"
  tf-idf cosine pre-filter -> candidate pairs (cos > 0.75, max 20)
  for each pair:
    LLMProvider.complete(consolidation_prompt) -> ReviewProposal(kind=merge|keep)
    persist
  write CuratorReviewRunRecord + Markdown report to blob curator/reviews/
release lock
```

Application is manual: `POST /v1/admin/curator/reviews/{id}/approve` calls `curator_review_apply.apply_patch_proposal` or `apply_merge_proposal`. Each applies via `replace_with_etag_retry` (proposals carry the original `_etag`; stale ⇒ `REVIEW_PROPOSAL_STALE`), bumps patch version, audits, invalidates caches. Rejections are recorded but never mutate skills.

NEVER calls `delete_item` or `delete_blob` — statically gated.

### 11.6 Rollback

`POST /v1/admin/curator/rollback` → load snapshot manifest → for each entry, restore Blob byte-for-byte and re-up Cosmos status — never deletes anything outside the snapshot's manifest set. Round-trip integrity is asserted by `backend/tests/integration/test_curator_rollback_round_trip.py`.

---

## 12. Workers

### Classifier (`backend/workers/classifier.py`)

- `BLPOP queue:classifier` with `CLASSIFIER_BLPOP_TIMEOUT_SECONDS` (default 5 s).
- On each pop: `read_item` doc → run classifier → `replace_item(status=classified, classification=…)` → audit → `DEL cache:skills:item:{id}`.
- Failures mark `classifier_status=failed`; janitor sweeps stuck `queued` docs by `JANITOR_CLASSIFIER_STALE_MULTIPLIER × BLPOP timeout`.

### Curator scheduler (`backend/workers/curator_scheduler.py`)

Single loop runs both passes back-to-back per cycle. Cadence: `@every:<seconds>` literal for local dev, otherwise a 24 h fallback (true cron parsing left to the Azure Function Timer trigger in prod).

```
while not stop:
  curator_svc.execute_pass(dry_run=False, ...)        # M2
  if review_provider:                                  # CURATOR_REVIEW_ENABLED
    curator_review_svc.execute_review_pass(...)        # M3
  await asyncio.wait_for(stop.wait(), timeout=sleep_s)
```

CuratorPaused / LockUnavailable are caught and logged — both are normal operational states. Review errors are caught per-pass so review failure never blocks the deterministic pass.

---

## 13. Configuration

Source: `backend/core/config.py` (`Settings`). Reads `.env.local` for local dev. Production reads real env vars / Key Vault references.

Notable groups:

```
Cosmos:        COSMOS_ENDPOINT, COSMOS_KEY (empty=AAD), COSMOS_DB_NAME, COSMOS_VERIFY_TLS
Blob:          BLOB_CONNECTION_STRING or BLOB_ACCOUNT_URL (identity),
               BLOB_PUBLISHED_CONTAINER, BLOB_ARCHIVE_CONTAINER, BLOB_SNAPSHOTS_CONTAINER
Redis:         REDIS_URL  OR
               REDIS_USE_ENTRA=true + REDIS_HOST + REDIS_PORT + REDIS_DB + REDIS_SSL
               + REDIS_ENTRA_USERNAME (object id) + REDIS_ENTRA_SCOPE (default https://redis.azure.com/.default)
App:           AUTH_MODE (stub|fake_oidc|oidc|saml), LOCAL_DEV, CLASSIFIER_PROVIDER (stub|llm),
               MAX_BUNDLE_BYTES, CORS_ORIGINS, LOG_LEVEL
Stub roles:    MANAGER_EMAILS, ADMIN_EMAILS  (comma-separated)
OIDC:          ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_GROUP_ID_ADMIN, OIDC_ISSUER/JWKS_URL (optional override)
API keys:      APIKEY_PEPPER, APIKEY_PREFIX (default sh_live_), APIKEY_CACHE_TTL_SECONDS
Telemetry:     APPINSIGHTS_CONNECTION_STRING, OTEL_SERVICE_ROLE
Worker tuning: CLASSIFIER_QUEUE_KEY, CLASSIFIER_BLPOP_TIMEOUT_SECONDS
Cache TTLs:    CACHE_LIST_TTL_SECONDS (60), CACHE_ITEM_TTL_SECONDS (300), PUBLISH_LOCK_TTL_SECONDS (30)
Curator (M2): CURATOR_STALE_DAYS (30), CURATOR_ARCHIVE_DAYS (90), CURATOR_LOCK_TTL_SECONDS,
               CURATOR_SNAPSHOT_RETENTION (5), CURATOR_SCHEDULE_CRON (@every:N or default 24h),
               JANITOR_CLASSIFIER_STALE_MULTIPLIER
Review (M3):  CURATOR_REVIEW_PROVIDER (foundry|fake),
               FOUNDRY_ENDPOINT (must end in /models), FOUNDRY_DEPLOYMENT, FOUNDRY_API_VERSION,
               AZURE_AI_FOUNDRY_API_KEY (empty=AAD),
               CURATOR_REVIEW_MAX_{INPUT,OUTPUT}_TOKENS,
               CURATOR_REVIEW_MAX_SKILLS_PER_RUN (50), CURATOR_REVIEW_MAX_TOTAL_TOKENS_PER_RUN (400k),
               CURATOR_REVIEW_AGENT_UPLOADER_PREFIX ("agent:"),
               CURATOR_REVIEW_CONSOLIDATION_{MIN_COSINE,MAX_PAIRS},
               CURATOR_REVIEW_SCHEDULE_CRON, CURATOR_REVIEW_ENABLED (default false)
```

Validation:

- `AUTH_MODE=oidc` requires `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_GROUP_ID_ADMIN` (`model_validator`).
- `enforce_production_safety()` rejects `stub`/`fake_oidc` unless `LOCAL_DEV=1`.

---

## 14. Infrastructure (Bicep)

`infra/main.bicep` composes per-environment stacks parameterised by `infra/parameters/{dev,staging,prod}.bicepparam`. Modules:

| Module | Provisions |
|--------|------------|
| `cosmos.bicep` | Cosmos account + DB + containers; `disableLocalAuth=true` in prod; RBAC role assignments via `rbac.bicep`. |
| `redis.bicep` | Azure Cache for Redis with `aadEnabled=true`; access policies for the app's Managed Identity. |
| `storage.bicep` | Storage account with shared-key disabled (prod); blob containers. |
| `appservice.bicep` | App Service Plan + Linux web app for the API; System-Assigned Managed Identity. |
| `worker.bicep` | Azure Function app for the curator scheduler (Timer trigger). |
| `frontend.bicep` / `staticwebapp.bicep` | Static Web App for Next.js. |
| `keyvault.bicep` | Key Vault for OIDC client secrets, API-key pepper, App Insights instrumentation key. |
| `appinsights.bicep` | App Insights + Log Analytics workspace. |
| `rbac.bicep` | All role assignments wired to the API + Worker MSIs: Cosmos Data Contributor, Storage Blob Data Owner, Redis Data Owner, Cognitive Services User. |

Deploy: `az deployment group what-if -g <rg> -f infra/main.bicep -p infra/parameters/<env>.bicepparam`.

---

## 15. Testing

`backend/tests/` is split into `unit/` (pure / fast) and `integration/` (runs against the local emulator stack).

### Unit (selection)

- `test_never_delete_invariant.py` — AST scans curator/rollback/snapshot/usage/janitor service + worker for any `delete_item(...)` or `delete_blob(...)`. Hard fail.
- `test_curator_planner.py` — pure deterministic planner: same inputs ⇒ same outputs; pinned skills never appear; status transitions are well-formed.
- `test_curator_review_*` — prompt rendering, similarity TF-IDF, proposal model validation, apply pipeline.
- `test_cosmos_etag_retry.py` — optimistic concurrency wrapper.
- `test_auth*`, `test_api_keys.py`, `test_config.py`, `test_errors.py`, `test_telemetry.py` — auth modes, settings, error codes.
- `test_skill_bundle.py`, `test_snapshot_determinism.py` — deterministic tar encoding.
- `test_redis_cache_keys.py` — key naming contract.

### Integration

- `test_cosmos_bootstrap.py` — `ensure_containers` against the emulator.
- `test_e2e_happy_path.py` — upload → classify → approve → download → usage round trip.
- `test_curator_run.py`, `test_curator_endpoints.py`, `test_curator_pin_unpin.py` — M2 surface.
- `test_curator_rollback_round_trip.py` — snapshot → mutate → rollback → byte-equal verification.
- `test_janitor_sweep.py` — re-queues Cosmos-pending docs.
- `test_redis_down_fallback.py` — kill Redis mid-test, catalog still serves from Cosmos.
- `test_redis_lock.py` — `redis_lock` mutual exclusion.
- `test_usage_pipeline.py` — counter math + TTL surface.

Pre-commit gates (AGENTS.md §10): `ruff check`, `ruff format --check`, full pytest, `pnpm lint`, `tsc --noEmit`, secrets scan. CI mirrors these.

---

## 16. Observability

- `backend/core/logging.py` — structured JSON logger with `contextvars`; `bind(skill_id=…, actor=…)` annotates every record on the current task.
- `backend/core/telemetry.py:configure_telemetry` — wires OpenTelemetry to Azure Monitor when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set; no-ops otherwise.
- `OTEL_SERVICE_ROLE` differentiates `api` from `worker` traces. The classifier worker sets it on import (`backend/workers/classifier.py:107`).
- Every state transition emits an `audit` row including `actor`, `actor_oid`, `before`/`after`, and a stable `action` string.

---

## 17. Local Development Workflow

```
# 1. Bring up the local stack (Cosmos emulator + Azurite + Redis 7).
docker compose up -d

# 2. Trust the Cosmos emulator cert (once).
./scripts/install_emulator_cert.sh

# 3. Run the API.
uv run uvicorn backend.app:create_app --factory --reload

# 4. Run the classifier worker (separate terminal).
uv run python -m backend.workers.classifier

# 5. (Optional) Run the curator scheduler.
uv run python -m backend.workers.curator_scheduler

# 6. Frontend.
pnpm --filter frontend dev

# 7. Tests.
uv run pytest
pnpm --filter frontend test
```

Stub auth defaults send `X-User-Email: contributor@org` from the frontend dev shell; admin actions need `X-User-Email: admin@org`.

To exercise the **Azure path** locally without deploying the app:

1. `az login` against the target tenant.
2. Set `.env.local` to point at real resources (passwordless config — leave `COSMOS_KEY`, `BLOB_CONNECTION_STRING`, `REDIS_URL`, `AZURE_AI_FOUNDRY_API_KEY` empty).
3. Make sure your user has: Cosmos Data Contributor, Storage Blob Data Owner, Redis Data Owner access policy, Cognitive Services User. The Bicep `rbac.bicep` module wires these for app MSIs in production.
4. Re-run uvicorn — `/healthz` should report `cosmos=ok, blob=ok, redis=ok`.

---

## 18. Invariants & Things Not To Touch

Re-read AGENTS.md §3–§5 before changing any of:

- `backend/services/publish.py` — canonical Cosmos-first ordering.
- `backend/services/curator.py`, `curator_rollback.py`, `snapshot.py` — never-delete invariant.
- `backend/core/redis.py:key_*` — key naming is a contract used across the worker, scheduler, and tests.
- `backend/core/cosmos.py:ensure_containers` — graceful-degrade behaviour is required for data-plane-only RBAC.
- `backend/tests/unit/test_never_delete_invariant.py` — AST gate. Do not weaken its scan.

If you find yourself wanting to write `delete_item` or `delete_blob` anywhere near skills or bundles: stop, re-read AGENTS.md §5, write archival logic instead.

---

## 19. Change Log

| Version | Date | Highlights |
|---------|------|-----------|
| 2.0 | 2026-05-16 | Full rewrite. Adds §10 passwordless auth (Cosmos AAD / Blob user-delegation SAS / Redis Entra credential provider / Foundry MI). Documents M3 LLM review (services, endpoints, data flow). Adds aiohttp<3.10 pin rationale and current container set incl. `review_proposals`. Replaces v1.0. |
| 1.0 | 2026-05-16 | Initial architecture map covering M0–M2 (POC, OIDC + API keys, curator lifecycle). |
