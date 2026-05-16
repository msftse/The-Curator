# Feature: M0 POC — End-to-End Skill Submission Flow

The following plan should be complete, but it is important that you validate documentation, codebase patterns, and task sanity before you start implementing. This is a greenfield M0 — no application code exists yet, so most tasks are CREATE-style. Pay special attention to naming of models, env vars, and module paths so later milestones (M1 auth, M2 curator) drop in cleanly.

## Feature Description

Stand up the first vertical slice of Agentic Skill Hub: a contributor uploads a SKILL.md bundle through a Next.js UI, the file lands as a pending document in Cosmos DB, a Python worker picks it up off a Redis queue and writes back a classification (stubbed deterministic output for M0, with a swappable real-LLM interface), a manager approves it in a review queue, a publish service packages the bundle as an immutable tar.gz into Azurite, and a public REST API lists/downloads the published skill. Everything must run end-to-end on `docker compose up` against the Cosmos DB emulator, Azurite, and `redis:7` with zero Azure spend.

The slice is intentionally narrow: no Entra ID (auth is the `X-User-Email` stub), no curator, no rate limiting, no Bicep — just the spine. But it must respect the Cosmos+Redis+Blob storage split and the four Redis rules so that M1-M4 work plugs in without rework.

## User Story

As a contributor I want to upload a SKILL.md bundle through a web UI and watch it move from `pending` → `classified` → `approved`, while a manager approves it and an agent runtime can list and download the published artifact through a REST API, so that the org has a working POC of the skill hub running entirely on local emulators.

## Problem Statement

There is no working implementation of the platform yet. The PRD (`docs/PRD.md` v0.2) and `AGENTS.md` define a strict architecture (Cosmos as system of record, Redis as cache + queue + locks only, Blob for immutable bundle bytes), but the codebase is a greenfield repo with only docs. We need to ship a demoable end-to-end flow on local emulators in two weeks (M0) that hard-codes the right patterns from day one, so future contributors cannot accidentally write to Redis as the source of truth or skip the audit log.

## Solution Statement

Build a minimal-but-correct vertical slice across all three storage layers, organized in the directory structure mandated by `AGENTS.md` §7. Backend is FastAPI with async I/O end-to-end and DI-injected storage clients in `backend/core/`. Business logic lives in `backend/services/`. A standalone `backend/workers/classifier.py` runs as a separate `python -m` process in dev. Frontend is Next.js 14 App Router with three pages (`/upload`, `/my-submissions`, `/admin/queue`) that hit the backend through a typed client. A `docker-compose.yml` brings up the Cosmos emulator, Azurite, and Redis. Every state transition writes an `audit` record. The classifier is stubbed deterministically behind a `ClassifierProvider` Protocol so the real LLM call is a one-file swap in a later milestone.

## Feature Metadata

**Feature Type**: New Capability (greenfield)
**Estimated Complexity**: High (cross-stack, emulator infra, four storage layers, async workers)
**Primary Systems Affected**: backend (FastAPI), frontend (Next.js), workers (classifier), docker-compose, Cosmos schemas, Blob layout, Redis keys
**Dependencies**:
- Python 3.12, `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `azure-cosmos>=4.7` (async), `azure-storage-blob>=12.20` (aio), `redis[hiredis]>=5` (asyncio), `python-multipart`, `pyyaml`, `httpx` (tests), `pytest`, `pytest-asyncio`, `ruff`
- Node 20+, Next.js 14 (App Router), Tailwind, TypeScript strict, `zod` (form validation)
- Docker images: `mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator:vnext-preview` (or `latest`), `mcr.microsoft.com/azure-storage/azurite:latest`, `redis:7-alpine`

---

## CONTEXT REFERENCES

### Relevant Codebase Files IMPORTANT: YOU MUST READ THESE FILES BEFORE IMPLEMENTING!

- `AGENTS.md` (entire file) — Project conventions. Especially:
  - §3 Cosmos+Redis+Blob split (non-negotiable)
  - §4 The four Redis rules (Cosmos-first, fallback on read, TTL everything, queue is the only exception)
  - §5 Never-delete invariant — not directly in scope for M0 but do not introduce delete code paths
  - §6 Local-first dev loop (zero Azure spend requirement)
  - §7 Directory structure (use exactly this layout)
  - §8 Patterns & Conventions (async everywhere, DI clients, audit on every transition)
  - §10 Pre-commit & test expectations
- `docs/PRD.md` v0.2 — Authoritative spec. Especially:
  - §6 Lifecycle of a skill (`upload → pending → classified → approved → published`)
  - §7.1–7.5 Upload, Classifier, Review Queue, Publish, Public Catalog API (M0 features)
  - §10 API Specification (endpoint paths, JSON shapes)
  - §10 Data Model — `skills`, `audit`, `usage_events` container schemas (lines 402–466). Field names here are normative.
  - §12 Phase M0 deliverables (lines 503–516)
- `.opencode/CONTEXT.md` — Decision history; consult when an architectural choice feels ambiguous.
- `README.md` — Short overview; no implementation guidance.

### New Files to Create

**Root / infra**
- `docker-compose.yml` — Cosmos emulator + Azurite + Redis 7
- `.env.local.example` — Documented env vars, copy to `.env.local`
- `.gitignore` — Python + Node + `.env.local` + emulator data dirs
- `pyproject.toml` — Backend Python project (PEP 621), ruff config, pytest config
- `Makefile` (optional but recommended) — `make up`, `make api`, `make worker`, `make web`, `make test`, `make seed`
- `.pre-commit-config.yaml` — Stub for ruff + prettier (M0 may leave empty hooks if time-boxed)

**Backend (`backend/`)**
- `backend/__init__.py`
- `backend/app.py` — FastAPI app factory; mounts routers, registers lifespan that initializes clients + Cosmos containers
- `backend/core/__init__.py`
- `backend/core/config.py` — `Settings` (pydantic-settings) reading `.env.local`
- `backend/core/cosmos.py` — Async Cosmos client + `ensure_containers()` for `skills`, `audit`, `usage_events`
- `backend/core/redis.py` — Async Redis client factory, key helpers, distributed-lock context manager
- `backend/core/blob.py` — Async Blob service client, container helpers (`published`, `archive`, `snapshots`)
- `backend/core/auth.py` — `get_current_user()` dependency reading `X-User-Email` (stub mode)
- `backend/core/deps.py` — FastAPI `Depends` wiring for cosmos / redis / blob clients
- `backend/core/errors.py` — Stable error codes + exception handlers returning `{error_code, message}`
- `backend/core/logging.py` — Structured JSON logger with `skill_id` + `actor` context vars
- `backend/models/__init__.py`
- `backend/models/skill.py` — Pydantic models matching PRD §10 (`SkillDoc`, `Classification`, `Bundle`, `UsageCounters`)
- `backend/models/audit.py` — `AuditRecord` model
- `backend/models/api.py` — Request/response DTOs (`UploadResponse`, `SkillListItem`, `ApproveRequest`, `RejectRequest`, `ClassificationPatch`)
- `backend/api/__init__.py`
- `backend/api/skills.py` — Public catalog routes: `GET /v1/skills`, `GET /v1/skills/{id}`, `GET /v1/skills/{id}/download` (out of scope for M0: `/versions`, `/usage` — leave stubs that 501)
- `backend/api/uploads.py` — `POST /v1/uploads`, `GET /v1/me/submissions`
- `backend/api/admin.py` — `GET /v1/admin/queue`, `POST /v1/admin/skills/{id}/approve`, `POST /v1/admin/skills/{id}/reject`, `PATCH /v1/admin/skills/{id}/classification`
- `backend/services/__init__.py`
- `backend/services/upload.py` — Validate bundle, parse SKILL.md frontmatter, write pending doc to Cosmos, enqueue classifier job
- `backend/services/publish.py` — Package tar.gz, upload to Blob, write `bundle` + flip status to `approved` in Cosmos, write audit; guarded by Redis lock
- `backend/services/catalog.py` — Read approved skills with Redis cache + Cosmos fallback; cache invalidation on publish
- `backend/services/audit.py` — `record(skill_id, action, actor, before, after, metadata)`; appends to `audit` container
- `backend/services/classifier_stub.py` — `StubClassifier` (deterministic: category from frontmatter `category` or "uncategorized", tags from frontmatter or empty, `quality_score=70`, `summary=first 140 chars of body`, `duplicate_candidates=[]`) — implements `ClassifierProvider` Protocol
- `backend/services/skill_bundle.py` — Pure helpers: parse SKILL.md (yaml frontmatter + body), validate required fields, normalize `skill_id` (slug from frontmatter `name`), compute sha256, build tar.gz from in-memory file map
- `backend/workers/__init__.py`
- `backend/workers/classifier.py` — `python -m backend.workers.classifier` entrypoint; BLPOP loop with timeout, fetches Cosmos doc, runs `ClassifierProvider`, writes classification back, writes audit, invalidates relevant Redis keys
- `backend/tests/__init__.py`
- `backend/tests/conftest.py` — Fixtures: app client, cosmos/redis/blob test clients pointing at compose stack, container cleanup
- `backend/tests/unit/test_skill_bundle.py`
- `backend/tests/unit/test_classifier_stub.py`
- `backend/tests/unit/test_redis_cache_keys.py`
- `backend/tests/integration/test_upload_flow.py` — upload → pending doc + audit + queue length == 1
- `backend/tests/integration/test_classifier_worker.py` — Run worker once, assert status flips to `classified`, audit recorded
- `backend/tests/integration/test_publish_flow.py` — approve → blob exists at versioned path, checksum matches, status `approved`, audit recorded, cache invalidated
- `backend/tests/integration/test_catalog_api.py` — list returns approved skill, second call hits cache (assert via monkeypatch on cosmos), download returns 307/signed URL pointing at Azurite
- `backend/tests/integration/test_redis_down_fallback.py` — With Redis paused, `/v1/skills` still serves from Cosmos

**Frontend (`frontend/`)**
- `frontend/package.json` — Next 14, React 18, Tailwind, TS strict, `zod`
- `frontend/tsconfig.json` — strict mode
- `frontend/next.config.mjs`
- `frontend/tailwind.config.ts`, `frontend/postcss.config.mjs`, `frontend/app/globals.css`
- `frontend/app/layout.tsx` — Top nav with stub user email picker (sets `localStorage` → injected as `X-User-Email`)
- `frontend/app/page.tsx` — Landing page linking to upload / my-submissions / admin queue
- `frontend/app/upload/page.tsx` — Drag-drop form (single SKILL.md or .zip/.tar.gz, max 10MB), shows pending response + classifier polling status
- `frontend/app/my-submissions/page.tsx` — Table of caller's submissions with status badges
- `frontend/app/admin/queue/page.tsx` — Pending review queue; per-row detail drawer renders SKILL.md preview, editable classification, approve/reject buttons
- `frontend/components/StatusBadge.tsx`
- `frontend/components/SkillPreview.tsx` — Renders SKILL.md markdown (use `react-markdown`)
- `frontend/lib/api/client.ts` — Typed fetch wrapper that injects `X-User-Email` from `localStorage`, exposes `uploads`, `mySubmissions`, `queue`, `approve`, `reject`, `patchClassification`, `listSkills`, `downloadUrl`
- `frontend/lib/api/types.ts` — TS mirrors of `models/api.py`

**Scripts**
- `scripts/seed_skills.py` — Inserts 3 sample pending skills + 1 approved skill for dev
- `scripts/wait_for_emulators.py` — Polls Cosmos / Azurite / Redis until ready; used by CI and `make up`
- `scripts/install_emulator_cert.sh` — Documented helper to trust Cosmos emulator TLS cert on macOS/Linux (or document `COSMOS_VERIFY_TLS=false` for local)

### Relevant Documentation YOU SHOULD READ THESE BEFORE IMPLEMENTING!

- [FastAPI Lifespan + Dependency Injection](https://fastapi.tiangolo.com/advanced/events/#lifespan)
  - Why: Cosmos/Redis/Blob clients are created in `lifespan`, attached to `app.state`, and injected via `Depends`. Do NOT instantiate clients inside request handlers.
- [FastAPI File Uploads](https://fastapi.tiangolo.com/tutorial/request-files/#uploadfile)
  - Why: `POST /v1/uploads` accepts `UploadFile`; use `await file.read()` with a streaming size guard.
- [azure-cosmos async client](https://learn.microsoft.com/python/api/overview/azure/cosmos-readme?view=azure-python#use-async-client)
  - Why: M0 uses the async client end-to-end. Container creation idempotency via `create_container_if_not_exists`.
- [azure-cosmos Emulator quickstart](https://learn.microsoft.com/azure/cosmos-db/local-emulator?tabs=docker-linux%2Cpython&pivots=api-nosql)
  - Why: Emulator endpoint is `https://cosmos:8081` inside compose network; well-known master key. TLS cert handling is the #1 gotcha.
- [Azurite README](https://learn.microsoft.com/azure/storage/common/storage-use-azurite?tabs=docker-hub)
  - Why: Connection string for the well-known dev account; signed URL generation works against Azurite the same as prod Blob.
- [azure-storage-blob async](https://learn.microsoft.com/python/api/overview/azure/storage-blob-readme?view=azure-python#async-clients)
  - Why: Use `aio` clients; generate user-delegation-style SAS for downloads (against Azurite, use account-key SAS).
- [redis-py asyncio + BLPOP](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html)
  - Why: Classifier worker uses `await redis.blpop("classifier:queue", timeout=5)`.
- [Redis SET NX with TTL for distributed locks](https://redis.io/docs/latest/develop/use/patterns/distributed-locks/)
  - Why: Publish must be guarded; use a simple `SET key value NX EX <ttl>` pattern (single Redis instance, no Redlock for M0). Document this is a "good-enough" lock.
- [Next.js 14 App Router Server Components](https://nextjs.org/docs/app/building-your-application/rendering/server-components)
  - Why: Default to server components; mark interactive pages (`/upload`, `/admin/queue`) `"use client"` only where forms/state require it.
- [react-markdown](https://github.com/remarkjs/react-markdown)
  - Why: Render SKILL.md in the review drawer.
- [Cosmos DB TTL on a container](https://learn.microsoft.com/azure/cosmos-db/nosql/time-to-live)
  - Why: `usage_events` container is created with default TTL = 90 days, but M0 may leave it unused — still set TTL on creation so M2 doesn't have to migrate.

### Patterns to Follow

**Naming Conventions**
- Python: snake_case modules, PascalCase classes, snake_case functions. Pydantic model class names are singular (`SkillDoc`, not `Skills`).
- TypeScript: camelCase functions/vars, PascalCase components and types. Files: `kebab-case.tsx` for app routes (Next.js conventions), PascalCase for components.
- Cosmos container names: lowercase plural (`skills`, `audit`, `usage_events`) — match PRD §10.
- Redis keys: `<resource>:<scope>:<id>` colon-delimited. M0 keys:
  - `cache:skills:list:v1` (hot list, 60s TTL)
  - `cache:skills:item:{skill_id}` (5min TTL)
  - `queue:classifier` (LIST)
  - `lock:publish:{skill_id}` (SET NX EX 30)
- Blob paths: exactly per PRD §6: `published/{skill_id}/{version}/bundle.tar.gz`, `archive/{skill_id}/{version}/`, `snapshots/{utc-iso}/skills.tar.gz`.

**Cosmos-First Write Pattern (mandatory — Redis rule #1)**
```python
# backend/services/publish.py — illustrative
async def publish(skill_id: str, actor: str, ...):
    async with redis_lock(f"lock:publish:{skill_id}", ttl=30):
        doc = await cosmos.skills.read(skill_id)
        # 1. Build artifact, upload to Blob
        tar_bytes, checksum = build_tar(doc.bundle_files)
        blob_url = await blob.put(f"published/{skill_id}/{doc.version}/bundle.tar.gz", tar_bytes)
        # 2. Cosmos write FIRST (source of truth)
        doc.status = "approved"
        doc.bundle = Bundle(blob_url=blob_url, checksum_sha256=checksum, size_bytes=len(tar_bytes), ...)
        await cosmos.skills.replace(doc)
        # 3. Audit
        await audit.record(skill_id, "publish", actor, before=..., after=...)
        # 4. Redis invalidation LAST (only after Cosmos succeeded)
        await redis.delete("cache:skills:list:v1", f"cache:skills:item:{skill_id}")
```

**Cosmos-Fallback Read Pattern (mandatory — Redis rule #2)**
```python
# backend/services/catalog.py — illustrative
async def list_approved():
    try:
        cached = await redis.get("cache:skills:list:v1")
        if cached:
            return json.loads(cached)
    except RedisError as e:
        log.warning("redis_unavailable_fallback_to_cosmos", err=str(e))
    items = [item async for item in cosmos.skills.query("SELECT * FROM c WHERE c.status='approved'")]
    try:
        await redis.set("cache:skills:list:v1", json.dumps(items), ex=60)
    except RedisError:
        pass  # cache failures are non-fatal
    return items
```

**Audit on Every Transition (mandatory — AGENTS.md §8)**
Any service that mutates skill status MUST call `audit.record(...)` in the same logical operation. Integration tests assert audit count increments.

**Error Handling**
- Domain errors raise typed exceptions in `backend/core/errors.py` (`SkillNotFound`, `InvalidBundle`, `AlreadyPublished`, `BundleTooLarge`).
- A FastAPI exception handler maps them to `{ "error_code": "SKILL_NOT_FOUND", "message": "..." }` with appropriate HTTP status.
- Log structured JSON with `skill_id` and `actor` context vars (use `contextvars`).

**TypeScript API Client**
```ts
// frontend/lib/api/client.ts — illustrative shape
export const api = {
  uploads: { create: (form: FormData) => post<UploadResponse>("/v1/uploads", form) },
  admin: {
    queue: () => get<SkillListItem[]>("/v1/admin/queue"),
    approve: (id: string) => post<void>(`/v1/admin/skills/${id}/approve`, {}),
    // ...
  },
};
```
No `any` without a `// eslint-disable-next-line` + justification comment.

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation (local stack + scaffolding)
Get emulators running, repo skeleton in place, smoke connectivity to all three storage layers.

**Tasks:**
- Author `docker-compose.yml` (Cosmos emulator + Azurite + Redis 7 with AOF)
- Author `.env.local.example` and document copy-to-`.env.local`
- Initialize `pyproject.toml` (backend deps, ruff, pytest config)
- Initialize `frontend/package.json` + Next 14 scaffold + Tailwind
- Create directory tree per AGENTS.md §7
- Implement `backend/core/{config,cosmos,redis,blob,auth,deps,errors,logging}.py`
- Implement `lifespan` that creates the three Cosmos containers and the two Blob containers (`published`, `archive`) idempotently
- Add `scripts/wait_for_emulators.py`

### Phase 2: Core Implementation (upload → classify → publish)
Land business logic with services, models, workers.

**Tasks:**
- Implement `backend/models/{skill,audit,api}.py`
- Implement `backend/services/skill_bundle.py` (frontmatter parse, tar build, sha256)
- Implement `backend/services/upload.py` (validate, write pending Cosmos doc FIRST, then `RPUSH queue:classifier`)
- Implement `backend/services/audit.py`
- Implement `backend/services/classifier_stub.py` + `ClassifierProvider` Protocol
- Implement `backend/workers/classifier.py` BLPOP loop
- Implement `backend/services/publish.py` (Redis lock, tar to Blob, Cosmos status flip, audit, cache invalidate)
- Implement `backend/services/catalog.py` (Redis cache + Cosmos fallback)

### Phase 3: Integration (API + UI + wiring)
Expose services via FastAPI routes and Next.js pages.

**Tasks:**
- Implement `backend/api/{uploads,admin,skills}.py` routers; register in `backend/app.py`
- Implement `frontend/lib/api/{client,types}.ts`
- Build `frontend/app/upload/page.tsx` (file picker, optimistic upload, poll status)
- Build `frontend/app/my-submissions/page.tsx`
- Build `frontend/app/admin/queue/page.tsx` (list + detail drawer + approve/reject)
- Add stub-user picker in layout (writes `X-User-Email` to localStorage)
- Add `scripts/seed_skills.py`

### Phase 4: Testing & Validation
Lock the invariants under tests so future PRs cannot regress them.

**Tasks:**
- Write unit tests for `skill_bundle`, `classifier_stub`, redis key helpers
- Write integration tests against the live emulator stack:
  - upload flow (Cosmos write happens before Redis enqueue)
  - worker classify
  - publish flow (blob exists, cosmos status, audit recorded, cache invalidated)
  - catalog cache hit + Cosmos fallback (Redis down)
  - audit count assertions on every transition
- Add `scripts/wait_for_emulators.py` call in pytest `conftest.py`
- Write a single end-to-end happy-path test (`backend/tests/integration/test_e2e_happy_path.py`) that drives the full upload → classify → approve → list → download lifecycle through the FastAPI app
- Add Makefile target `make demo` that runs the e2e test and prints the resulting blob URL

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### CREATE `docker-compose.yml`
- **IMPLEMENT**: Three services — `cosmos` (Cosmos emulator vnext or linux preview image), `azurite` (blob+queue+table on default ports), `redis` (`redis:7-alpine` with `--appendonly yes`). Expose `8081` (Cosmos), `10000` (blob), `6379` (Redis). Add named volumes for Redis AOF persistence.
- **PATTERN**: Documented per `AGENTS.md` §6.
- **GOTCHA**: Cosmos emulator on Linux/macOS has TLS quirks; allow `COSMOS_VERIFY_TLS=false` in dev. Cosmos emulator typically needs `AZURE_COSMOS_EMULATOR_PARTITION_COUNT=3` and several minutes to start; use a healthcheck.
- **VALIDATE**: `docker compose up -d && python scripts/wait_for_emulators.py` exits 0.

### CREATE `.env.local.example`
- **IMPLEMENT**: Document every env var listed in PRD §9 + a `COSMOS_DB_NAME=skillhub`, `COSMOS_VERIFY_TLS=false`, `BLOB_CONNECTION_STRING=<azurite-default>`, `REDIS_URL=redis://localhost:6379/0`, `AUTH_MODE=stub`, `CLASSIFIER_PROVIDER=stub`, `MAX_BUNDLE_BYTES=10485760`.
- **GOTCHA**: Use the well-known Azurite + Cosmos emulator credentials; do NOT invent your own.
- **VALIDATE**: `cp .env.local.example .env.local && python -c "from backend.core.config import Settings; Settings()"` succeeds.

### CREATE `pyproject.toml`
- **IMPLEMENT**: PEP 621 metadata, deps listed in Feature Metadata, `[tool.ruff]` with `line-length=100`, `[tool.pytest.ini_options] asyncio_mode = "auto"`, optional `[tool.pyright]`.
- **VALIDATE**: `uv sync` (or `pip install -e .[dev]`) succeeds; `ruff check .` exits 0 on empty repo.

### CREATE `backend/core/config.py`
- **IMPLEMENT**: `Settings(BaseSettings)` (pydantic-settings v2) reading `.env.local`. Fields mirror env vars above. Provide `@lru_cache` `get_settings()`.
- **VALIDATE**: `python -c "from backend.core.config import get_settings; print(get_settings().redis_url)"` prints expected.

### CREATE `backend/core/cosmos.py`
- **IMPLEMENT**: `async def get_cosmos_client(settings) -> CosmosClient`; `async def ensure_containers(client, db_name)` that creates `skills` (PK `/skill_id`), `audit` (PK `/skill_id`), `usage_events` (PK `/skill_id`, default TTL 90*86400 seconds).
- **GOTCHA**: Use `azure.cosmos.aio.CosmosClient`. With emulator + `verify_tls=false`, pass `connection_verify=False` and silence urllib3 warnings.
- **VALIDATE**: `pytest backend/tests/integration/test_cosmos_bootstrap.py -k ensures_three_containers` passes.

### CREATE `backend/core/redis.py`
- **IMPLEMENT**: `async def get_redis(settings) -> Redis`. `@asynccontextmanager redis_lock(redis, key, ttl)` using `SET key <uuid> NX EX ttl` + delete-if-token-matches Lua snippet on exit.
- **PATTERN**: Single-instance lock per Redis docs.
- **GOTCHA**: Always wrap Redis calls in try/except for read paths (rule #2). Lock acquisition failure raises `LockUnavailable`.
- **VALIDATE**: Unit test acquires + releases lock; second concurrent attempt raises.

### CREATE `backend/core/blob.py`
- **IMPLEMENT**: `async def get_blob_service(settings) -> BlobServiceClient`; helpers `put_published(skill_id, version, data) -> str`, `signed_download_url(skill_id, version, ttl_minutes=15) -> str`, `ensure_containers()`.
- **GOTCHA**: Against Azurite, use account-key SAS via `generate_blob_sas(account_name="devstoreaccount1", account_key=AZURITE_KEY, ...)`. Document this clearly in code comments; M1 will swap to user-delegation SAS.
- **VALIDATE**: Integration test puts a blob and downloads via the returned URL with `httpx`.

### CREATE `backend/core/auth.py`
- **IMPLEMENT**: `class User(BaseModel): email: str; roles: list[Literal["contributor","manager","admin"]]`. `def get_current_user(x_user_email: str = Header(...)) -> User` — derive roles from a hardcoded map for M0 (`manager@org → ["contributor","manager","admin"]`, anyone else → `["contributor"]`). Raise 401 if missing in `stub` mode.
- **VALIDATE**: Unit test calls dep with missing header → 401.

### CREATE `backend/core/deps.py`, `backend/core/errors.py`, `backend/core/logging.py`
- **IMPLEMENT**: DI factories returning `app.state.cosmos`, etc. Exception classes + handler. JSON logger with context-var injection.
- **VALIDATE**: `pytest backend/tests/unit/test_errors.py` confirms error code shape.

### CREATE `backend/models/{skill,audit,api}.py`
- **IMPLEMENT**: Pydantic models per PRD §10. `SkillDoc` validates `status` against the literal set, `classification` is `Optional[Classification]`. `AuditRecord.action` is `Literal[...]` of the 11 actions. API DTOs are derived (do not leak Cosmos internal fields like `_etag`).
- **VALIDATE**: Round-trip a sample fixture JSON through `SkillDoc.model_validate` then `.model_dump(mode="json")` and assert equality.

### CREATE `backend/services/skill_bundle.py`
- **IMPLEMENT**: `parse_skill_md(text) -> (frontmatter: dict, body: str)` using `yaml.safe_load` between `---` markers. Validate required keys `name`, `description`. `slugify(name) -> skill_id`. `build_tar(files: dict[str,bytes]) -> (bytes, sha256)`. `BUNDLE_MAX = settings.max_bundle_bytes`.
- **GOTCHA**: SKILL.md may have no frontmatter — raise `InvalidBundle("missing frontmatter")`.
- **VALIDATE**: Unit tests cover happy path, missing frontmatter, missing required field, oversize bundle.

### CREATE `backend/services/audit.py`
- **IMPLEMENT**: `async def record(cosmos, skill_id, action, actor, before=None, after=None, metadata=None)`. Append-only `create_item`.
- **VALIDATE**: Integration test creates a record and queries by `skill_id`.

### CREATE `backend/services/upload.py`
- **IMPLEMENT**: `async def handle_upload(file, uploader, cosmos, redis) -> SkillDoc`. Steps: parse bundle → build `SkillDoc(status="pending", classifier_status="queued", ...)` → **Cosmos `create_item` FIRST** → audit `upload` → **then** `redis.rpush("queue:classifier", skill_id)`.
- **GOTCHA**: If Redis enqueue fails after Cosmos write succeeded, do NOT roll back Cosmos — log + rely on M2 janitor sweep. For M0, surface a `WARN` log and still return 201 to the client.
- **VALIDATE**: Integration test: POST upload → Cosmos has pending doc → audit has 1 entry → `LLEN queue:classifier == 1`.

### CREATE `backend/workers/classifier.py`
- **IMPLEMENT**: `async def main()` opens cosmos+redis, loops `await redis.blpop("queue:classifier", timeout=5)`. On each item: fetch doc, run `StubClassifier.classify(skill_md_text)`, set `doc.classification`, `doc.status="classified"`, `doc.classifier_status="done"`, Cosmos `replace_item`, audit `classify`, `redis.delete("cache:skills:item:{id}")` (list cache is approved-only, no need to bust).
- **GOTCHA**: On exception, mark `classifier_status="failed"` in Cosmos and record audit; do NOT crash the loop. Use exponential backoff on Cosmos errors.
- **VALIDATE**: Integration test enqueues a known skill_id, runs `main()` for one iteration via `asyncio.wait_for(main(), timeout=10)` with a stop sentinel, asserts status flip.

### CREATE `backend/services/publish.py`
- **IMPLEMENT**: `async def publish(skill_id, actor, cosmos, blob, redis)`. Acquire `redis_lock("lock:publish:{skill_id}", ttl=30)`. Read doc, refuse if already `approved` (idempotent: if Blob already exists with same checksum, return without re-uploading). Build tar from stored bundle bytes (M0: bundle bytes are stored on the Cosmos doc as base64, since we have no separate uploads container yet — see note below), upload to Blob, set `doc.bundle`, `doc.status="approved"`, `doc.approved_at`, `doc.approver`. Cosmos replace → audit `approve` + `publish` → invalidate `cache:skills:list:v1` and `cache:skills:item:{id}`.
- **PATTERN**: See "Cosmos-First Write Pattern" above.
- **GOTCHA**: For M0, "where do the original bundle bytes live between upload and publish?" The simplest correct answer: store the raw uploaded tar.gz as base64 on the pending Cosmos doc under `pending_bundle_b64` (small files only, capped at MAX_BUNDLE_BYTES). On publish, decode, re-tar (deterministic ordering for reproducible checksums), upload to Blob, then null out `pending_bundle_b64`. Document this as a M0-only shortcut to be replaced in M1 with a `staging/` Blob container.
- **VALIDATE**: Integration test approves a pending skill → blob exists at expected path → sha256 matches → Cosmos status `approved` → 2 audit records added → `cache:skills:list:v1` was deleted.

### CREATE `backend/services/catalog.py`
- **IMPLEMENT**: `list_approved(cursor, limit)`, `get_skill(skill_id)`. Cache + fallback per pattern above.
- **VALIDATE**: Integration test asserts second call within TTL does not query Cosmos (monkeypatch cosmos.query to raise after first call).

### CREATE `backend/api/uploads.py`
- **IMPLEMENT**: `POST /v1/uploads` (multipart), `GET /v1/me/submissions`. Use `get_current_user` dep.
- **VALIDATE**: `pytest backend/tests/integration/test_upload_flow.py` passes.

### CREATE `backend/api/admin.py`
- **IMPLEMENT**: `GET /v1/admin/queue` (status=pending|classified), `POST /v1/admin/skills/{id}/approve` (calls `publish.publish`), `POST /v1/admin/skills/{id}/reject` (sets status `rejected` + reason, audit), `PATCH /v1/admin/skills/{id}/classification` (merge override into `classification`, audit). All require manager role.
- **VALIDATE**: Integration test as contributor → 403; as manager → 200.

### CREATE `backend/api/skills.py`
- **IMPLEMENT**: `GET /v1/skills`, `GET /v1/skills/{id}`, `GET /v1/skills/{id}/download` returns `307` redirect to signed URL. Stub `/versions` and `/usage` to return 501 with `error_code=NOT_IMPLEMENTED_M0`.
- **VALIDATE**: Integration test downloads bundle through the redirect and sha256 matches.

### CREATE `backend/app.py`
- **IMPLEMENT**: `create_app()` factory. `lifespan` creates clients, calls `ensure_containers`, attaches to `app.state`. Registers routers, exception handlers, JSON logging middleware. CORS open to `http://localhost:3000` in dev.
- **VALIDATE**: `uvicorn backend.app:create_app --factory --reload` boots; `GET /healthz` returns `{"ok": true, "cosmos": "ok", "redis": "ok", "blob": "ok"}`.

### CREATE Frontend scaffolding
- **IMPLEMENT**: `pnpm create next-app frontend --ts --tailwind --app --src-dir false --import-alias "@/*"` (run manually or replicate output). Set `tsconfig` `"strict": true`.
- **VALIDATE**: `pnpm --filter frontend dev` serves at `:3000`.

### CREATE `frontend/lib/api/{client,types}.ts`
- **IMPLEMENT**: Typed fetch client; mirror DTOs.
- **VALIDATE**: `pnpm --filter frontend typecheck` (alias for `tsc --noEmit`) passes.

### CREATE `frontend/app/{upload,my-submissions,admin/queue}/page.tsx`
- **IMPLEMENT**: Per PRD §5 user stories. Upload page polls `GET /v1/me/submissions` every 3s for ~2 minutes after upload to show the classifier status flipping.
- **VALIDATE**: Manual: drag a SKILL.md, see status walk `pending → classified` within ~10s with stub classifier; in `/admin/queue` as `manager@org`, click approve, then see it appear in `GET /v1/skills`.

### CREATE `scripts/seed_skills.py` and `scripts/wait_for_emulators.py`
- **IMPLEMENT**: Seed inserts pending + classified + approved sample skills (idempotent). Wait script polls for up to 120s.
- **VALIDATE**: `python scripts/seed_skills.py` is idempotent on second run.

### CREATE end-to-end test `backend/tests/integration/test_e2e_happy_path.py`
- **IMPLEMENT**: ASGI test client posts an upload → runs classifier worker for one tick → manager approves → asserts blob exists → calls `GET /v1/skills` → calls download URL via `httpx.AsyncClient` → sha256 matches.
- **VALIDATE**: `pytest backend/tests/integration/test_e2e_happy_path.py -v` passes against the local emulator stack.

### UPDATE `README.md`
- **IMPLEMENT**: Quickstart: `cp .env.local.example .env.local && docker compose up -d && make api & make worker & make web`. Point at this plan and PRD.
- **VALIDATE**: A fresh contributor can follow the README and demo the happy path in <15 minutes.

---

## TESTING STRATEGY

### Unit Tests
- `skill_bundle.parse_skill_md` — happy, missing frontmatter, missing required key, oversize
- `classifier_stub.classify` — deterministic output shape
- `redis` lock helper — acquire/release, contention
- `errors` — error code mapping
- All unit tests run without docker compose (no live emulators required).

### Integration Tests
- Require `docker compose up -d` + `scripts/wait_for_emulators.py` (enforced in `conftest.py`)
- One file per service: `test_upload_flow`, `test_classifier_worker`, `test_publish_flow`, `test_catalog_api`, `test_redis_down_fallback`, `test_e2e_happy_path`
- Each test cleans its own Cosmos partition + Redis keys + Blob prefix in a fixture (no shared state)

### Edge Cases
- Upload of malformed YAML frontmatter → 400 with `error_code=INVALID_BUNDLE`
- Upload over `MAX_BUNDLE_BYTES` → 413 with `error_code=BUNDLE_TOO_LARGE`
- Approve when bundle already published with same checksum → idempotent no-op, no second audit row
- Approve while another approve is in-flight → second call raises `LOCK_UNAVAILABLE`
- Redis paused mid-test → `GET /v1/skills` still serves from Cosmos (degraded, not broken)
- Worker hits Cosmos failure → status `classifier_status=failed`, audit recorded, loop continues
- Upload writes Cosmos doc but Redis enqueue fails → request returns 201 with warning; pending doc visible in Cosmos (M2 janitor will re-queue)

---

## VALIDATION COMMANDS

Execute every command in order. Treat any non-zero exit as a stop.

### Level 1: Syntax & Style
```bash
uv run ruff format --check .
uv run ruff check .
pnpm --filter frontend lint
pnpm --filter frontend typecheck   # tsc --noEmit
```

### Level 2: Unit Tests
```bash
uv run pytest backend/tests/unit -v
pnpm --filter frontend test --if-present
```

### Level 3: Integration Tests
```bash
docker compose up -d
python scripts/wait_for_emulators.py
uv run pytest backend/tests/integration -v
```

### Level 4: Manual Validation
1. `docker compose up -d`
2. `uv run uvicorn backend.app:create_app --factory --reload` in one terminal
3. `python -m backend.workers.classifier` in another terminal
4. `pnpm --filter frontend dev` in a third
5. Open `http://localhost:3000/upload`; set stub user to `alice@org`; drag a sample SKILL.md from `scripts/fixtures/`; submit
6. Within ~10s, refresh `/my-submissions` — status should be `classified`
7. Set stub user to `manager@org`; open `/admin/queue`; click Approve
8. `curl http://localhost:8000/v1/skills | jq` should list the new skill
9. `curl -L http://localhost:8000/v1/skills/<id>/download -o bundle.tar.gz`
10. `sha256sum bundle.tar.gz` matches the checksum reported by `/v1/skills/<id>`

### Level 5: Additional Validation (Optional)
- `gh act -j ci` (if a GitHub Actions workflow is added)
- Use the `agent-browser` skill to script the manual flow end-to-end
- Use the `e2e-test` skill once the slice is wired up to take screenshots of each page

---

## ACCEPTANCE CRITERIA

- [ ] `docker compose up -d` brings up Cosmos emulator, Azurite, and Redis 7 with AOF; `scripts/wait_for_emulators.py` exits 0 within 120s
- [ ] `uv run uvicorn backend.app:create_app --factory` boots and `GET /healthz` reports all three storage layers OK
- [ ] `POST /v1/uploads` writes a pending doc to Cosmos FIRST, then enqueues classifier job; both observable through the test suite
- [ ] `python -m backend.workers.classifier` consumes from the queue and flips status to `classified` with deterministic stub output
- [ ] `POST /v1/admin/skills/{id}/approve` packages a tar.gz, uploads it to Azurite at `published/{skill_id}/{version}/bundle.tar.gz`, writes checksum to Cosmos, and records audit
- [ ] `GET /v1/skills` returns approved skills with a Redis cache hit on the second call and a Cosmos fallback when Redis is paused
- [ ] `GET /v1/skills/{id}/download` returns a 307 to a working Azurite SAS URL whose response sha256 matches Cosmos
- [ ] Every state transition (`upload`, `classify`, `approve`, `reject`, `publish`) writes exactly one row to the `audit` container — asserted in integration tests
- [ ] Redis rule #1: integration test confirms publish does not invalidate Redis before Cosmos succeeds (inject a Cosmos failure and assert cache is NOT invalidated)
- [ ] Redis rule #2: integration test stops Redis and confirms `/v1/skills` still returns 200
- [ ] Redis rule #3: every Redis key set by the app has a TTL; a unit test asserts via `redis.ttl(key) > 0`
- [ ] Redis rule #4 mitigations: pending doc exists in Cosmos before enqueue (verified by ordering test that intercepts Redis), and queue runs with AOF enabled (verified via `CONFIG GET appendonly` in a smoke test)
- [ ] Next.js UI walks a contributor through upload → status visibility, and a manager through queue → approve, end-to-end, against the local stack
- [ ] All `Level 1`, `Level 2`, `Level 3` validation commands pass with zero errors and zero new lint warnings
- [ ] No Azure credentials required at any point during M0
- [ ] No delete code paths anywhere — only status transitions and (future) archival

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order
- [ ] Each task validation passed immediately
- [ ] All validation commands executed successfully
- [ ] Full test suite passes (unit + integration)
- [ ] No linting or type-checking errors
- [ ] Manual happy-path validation completed and screenshotted
- [ ] Acceptance criteria all met
- [ ] README updated with quickstart
- [ ] No secrets committed (verify with `git diff --stat` + a grep for keys)
- [ ] Pre-commit hooks (when present) run cleanly without `--no-verify`

---

## NOTES

**Design decisions / trade-offs**

- **Bundle bytes between upload and publish live as base64 on the Cosmos pending doc.** This is the simplest correct answer for M0 (skills are capped at 10 MB). M1 should replace this with a `staging/{skill_id}/{upload_id}/bundle.tar.gz` Blob container and store only the staging URL on the Cosmos doc. Called out explicitly in `publish.py` with a `TODO(M1)` comment.
- **Stub classifier first, real LLM later.** `ClassifierProvider` is a Protocol with `StubClassifier` as the M0 implementation. M3 swaps in a real aux-model client; no code outside `services/classifier_*.py` should change.
- **Single-instance Redis lock (SET NX EX) instead of Redlock.** Acceptable for M0/M1 on a single Azure Cache instance. Document the trade-off in `redis.py`.
- **List cache key is versioned (`cache:skills:list:v1`).** When the list shape changes, bump to `v2` instead of doing a migration — Redis is regenerable.
- **Cosmos emulator TLS:** documented `COSMOS_VERIFY_TLS=false` as the supported local mode to avoid asking every contributor to install a cert. Prod uses real Cosmos with valid TLS.
- **Audit append-only:** enforced by convention and code review, not by Cosmos schema. M1 should add a stored procedure or RBAC that denies updates/deletes on the `audit` container.
- **No `usage_events` ingest path in M0.** The container is created with TTL but never written to. The `POST /v1/skills/{id}/usage` route returns 501. M2 brings it online.
- **Frontend auth is a localStorage email picker.** Pragmatic for the POC; M1 swaps in Entra ID with a `useUser()` hook that reads OIDC claims. Backend behind a single env flag (`AUTH_MODE`).
- **No production hardening:** no rate limiting, no CORS allow-list beyond localhost, no secret scanning. All are M1+ scope.

**Confidence**: 8/10 that an execution agent can land this in one pass. The Cosmos emulator + Azurite SAS combo is the most likely source of friction; the plan calls those gotchas out explicitly. If the agent gets stuck, the first place to look is `wait_for_emulators.py` returning healthy too early.
