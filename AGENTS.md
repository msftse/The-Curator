# AGENTS.md — Agentic Skill Hub

Rules and conventions for any agent (or human) contributing to this repository.
Authoritative context: `docs/PRD.md` (v0.2) and `.opencode/CONTEXT.md`. Read those first when in doubt.

---

## 1. Project Overview

**Agentic Skill Hub** is an internal web platform for submitting, reviewing, publishing, and maintaining reusable agent skills (SKILL.md bundles compatible with Hermes Agent and similar frameworks).

End-to-end flow: `upload → classify (auto) → manager review → publish (immutable tar.gz) → public catalog API → curator (lifecycle maintenance)`.

Users: **Contributor**, **Manager**, **Consumer (agent runtime)**, **Admin**.

MVP target: full local-emulator round-trip in 2 weeks (M0). See `docs/PRD.md` §12 for milestone breakdown.

---

## 2. Tech Stack

| Layer | Choice |
|-------|--------|
| Backend | **FastAPI** (Python 3.12) |
| Frontend | **Next.js 14** + Tailwind |
| System of Record | **Azure Cosmos DB for NoSQL** |
| Cache + queue + locks | **Azure Cache for Redis** (AOF enabled on queue) |
| Object storage | **Azure Blob Storage** |
| Background jobs | Azure Functions (prod) / Python worker process (local dev) |
| Auth | Entra ID OIDC (humans) + API keys (agents); POC uses `X-User-Email` header stub |
| Local dev | docker-compose: Cosmos DB emulator + Azurite + `redis:7` |
| Infra-as-code | Bicep |
| CI/CD | GitHub Actions |

Assume Python 3.12 + Node 20+. Do not pull in alternate runtimes (no Bun, Deno, etc.).

---

## 3. Architecture — Cosmos + Redis + Blob (Non-negotiable Split)

This split is the spine of the system. **Do not violate it.** Full rationale in `docs/PRD.md` §6.

### Cosmos DB — System of Record
Everything durable lives here first.
- Skill metadata (pending → classified → approved → rejected → stale → archived)
- Append-only `audit` container (no updates, no deletes)
- `usage_events` (raw, TTL 90 days) + aggregated counters on the skill doc
- Pinning state, classification, version history

Containers (see §10 of PRD for schemas):
- `skills`        — partition key `/skill_id`
- `audit`         — partition key `/skill_id`, append-only
- `usage_events`  — partition key `/skill_id`, TTL 90 days

### Redis — Cache + Ephemeral Coordination Only
Never the only copy of anything.
- Hot catalog list responses (60s TTL)
- Single-skill metadata lookups (5min TTL)
- Classifier job queue (LIST + BLPOP, AOF enabled)
- Rate-limit counters (sliding window with TTL)
- Web UI session tokens
- Distributed locks (`SET NX` with TTL) for publish + curator

### Azure Blob Storage — Immutable Artifact Bytes Only
- `published/{skill_id}/{version}/bundle.tar.gz`
- `snapshots/{utc-iso}/skills.tar.gz`
- `archive/{skill_id}/{version}/`

---

## 4. The Four Non-Negotiable Redis Rules

Every PR touching the data layer must obey these. Reviewers should reject violations on sight.

1. **Cosmos-first writes.** A write ALWAYS hits Cosmos first. Redis invalidation happens *after* the Cosmos write succeeds. Never write to Redis as the source of truth.
2. **Cache misses are normal, not errors.** Every Redis read path MUST have a Cosmos fallback. If Redis is down, the app gets slower — it does not break.
3. **TTL everything.** No infinite-lived keys in Redis. Worst case, the cache rebuilds in N seconds.
4. **Classifier queue is the one exception** for in-flight data. Mitigations are mandatory:
   - AOF persistence enabled on the queue.
   - Upload handler writes the pending doc to Cosmos *before* pushing to the Redis queue.
   - A janitor sweep scans Cosmos for `classifier_status=pending` docs older than threshold and re-queues them.

---

## 5. Never-Delete Invariant (Curator)

The curator can archive, suggest, and snapshot — it **never deletes**.

Hard rules:
- **No auto-delete, ever.** Worst possible outcome is archival, which is fully recoverable.
- **Pinned skills are immune** to every auto-transition and every curator suggestion.
- **Snapshot before every real pass.** Full tar.gz of the published Blob tree to `snapshots/{utc-iso}/`. Retain N (default 5).
- **Dry-run mode produces a report with zero mutations.**
- **Rollback must round-trip byte-for-byte** from the most recent snapshot.

Curator transitions are deterministic:
- No loads in 30 days → `stale`
- No loads in 90 days → `archived` (blob moved to `archive/`, Cosmos status flipped)

Admin commands: `pause`, `resume`, `run --dry-run`, `run`, `rollback`, `pin`, `unpin`, `restore`.

If you are tempted to add a delete code path anywhere near skills or bundles: stop, re-read this section, and write archival logic instead.

This invariant is enforced statically by `backend/tests/unit/test_never_delete_invariant.py`, which AST-scans the curator/rollback/snapshot/usage/janitor service + worker files for `delete_item(...)` and `delete_blob(...)` calls. Adding either is a hard test failure.

---

## 6. Local-First Dev Loop

The entire system must run on local emulators with **zero Azure spend**. This is a first-class requirement, not a nice-to-have.

- `docker-compose.yml` brings up: Cosmos DB emulator + Azurite (Blob) + `redis:7`.
- `.env.local` is the single source of dev config, consumed by backend and compose.
- `AUTH_MODE=stub` (default for local) uses an `X-User-Email` header instead of OIDC.
- Background workers run as a local Python process in dev; Azure Functions only in prod.
- New features MUST be demoable end-to-end on the local stack before being considered done.

If a change can only be verified against real Azure, it is not M0/M1-ready.

---

## 7. Suggested Directory Structure

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
├── docker-compose.yml
└── docs/PRD.md
```

Keep route modules thin. Business logic lives in `services/`. Storage clients live in `core/` and are dependency-injected.

---

## 8. Patterns & Conventions

### Python (backend)
- Python 3.12, type hints required on public functions.
- Format with `ruff format`, lint with `ruff check`.
- Pydantic models for request/response and Cosmos docs.
- Async I/O end-to-end (FastAPI, async Cosmos + Redis + Blob clients). Do not block the event loop.
- Storage clients are injected via FastAPI `Depends` — never instantiate inside business logic.

### TypeScript (frontend)
- Strict mode on. No `any` without an explicit comment justifying it.
- Server Components by default; `"use client"` only when needed.
- Tailwind utility classes; avoid bespoke CSS.
- API calls go through a typed client in `frontend/lib/api/`.

### Errors & Audit
- Every state transition (`upload`, `classify`, `approve`, `reject`, `publish`, `archive`, `pin`, `unpin`, `restore`, `rollback`) writes to the `audit` container. No transition without an audit record.
- Surface user-facing errors with a stable error code; log structured JSON with `skill_id` and `actor`.

### Security
- All secrets via env vars (12-factor). Production secrets in Azure Key Vault.
- Never proxy bundle bytes through the app tier — use signed Blob URLs.
- Pre-publish secret scan runs as part of the publish job.

---

## 9. Commands

Adapt these as concrete scripts land; treat as the contract the dev loop must satisfy.

| Task | Expected command |
|------|------------------|
| Bring up local stack | `docker compose up -d` |
| Backend dev server | `uv run uvicorn backend.app:app --reload` (or `python -m uvicorn …`) |
| Frontend dev server | `pnpm --filter frontend dev` |
| Run all tests | `uv run pytest` and `pnpm --filter frontend test` |
| Lint + format | `uv run ruff check . && uv run ruff format --check .` and `pnpm lint` |
| Type-check frontend | `pnpm --filter frontend typecheck` |
| Build infra plan | `az deployment group what-if … -f infra/main.bicep` |

---

## 10. Pre-commit & Test Expectations

Before opening a PR or marking work complete:

1. **Format + lint pass clean.** `ruff format` + `ruff check` for Python; `eslint` + `prettier` (or Next.js lint) for the frontend. Zero new warnings.
2. **Type-check passes.** `pyright`/`mypy` for backend (if configured), `tsc --noEmit` for frontend.
3. **Tests pass locally.** New code ships with tests:
   - Unit tests for services and pure functions.
   - Integration tests for any code that touches Cosmos / Redis / Blob, run against the local emulator stack.
   - At minimum one end-to-end happy-path test per user-facing flow.
4. **Local stack demo.** New user-visible behavior is demoable on `docker compose up` with no Azure credentials.
5. **Audit + invariants verified.** Any state transition added is covered by an audit-log assertion. Any curator change is covered by a dry-run-vs-real diff test and a snapshot/rollback round-trip test.
6. **Pre-commit hooks** (once configured in `.pre-commit-config.yaml`) MUST run cleanly. Do not bypass with `--no-verify`.
7. **No secrets in commits.** Pre-publish secret scan must pass; do the same locally.

CI (GitHub Actions, M1+) enforces all of the above. Local discipline keeps CI green.

---

## 11. Key Files

| File | Why it matters |
|------|----------------|
| `docs/PRD.md` | Authoritative product + architecture spec (v0.2). Read before non-trivial changes. |
| `.opencode/CONTEXT.md` | Original requirements conversation; preserves intent behind decisions. |
| `.opencode/commands/` | Slash-command workflows (`/create-prd`, `/create-rules`, etc.). |
| `docker-compose.yml` | Local emulator stack contract. |
| `infra/` | Bicep templates (forthcoming, M1). |
| `backend/core/` | Cosmos / Redis / Blob client wiring — touch carefully. |
| `backend/services/curator.py` | Implements the never-delete invariant; changes require extra scrutiny. |

---

## 12. When In Doubt

- Architecture question → `docs/PRD.md` §6.
- Why a decision was made → `.opencode/CONTEXT.md`.
- Storage placement question → §3 + §4 of this file.
- About to write a delete? → §5. Don't.
- About to write to Redis without Cosmos? → §4. Don't.
