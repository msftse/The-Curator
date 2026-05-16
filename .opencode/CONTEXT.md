# Conversation Context — Agentic Skill Hub

This file captures the requirements discussion between Michael Liav and Hermes that led to this project. Use it as the source for the `/create-prd` command and downstream planning.

## Project Name
**Agentic Skill Hub**

## What it is
Internal web platform for submitting, reviewing, publishing, and maintaining reusable agent skills (SKILL.md bundles compatible with Hermes Agent and similar agentic frameworks).

## Core flow
1. Anyone in the org uploads a skill (SKILL.md, optionally with bundle files: references/, templates/, scripts/) via the web UI.
2. The submission lands in **Cosmos DB** as a pending document with metadata (uploader, timestamp, status=pending).
3. A **classifier agent** runs automatically on upload, reads the SKILL.md, and assigns:
   - category (from a controlled taxonomy)
   - tags (free-form, max 8)
   - quality_score (0-100)
   - summary (one sentence)
   - duplicate_candidates (similar existing skills)
4. A **manager** reviews pending skills in a queue UI, sees the classifier's output, can override it, and approves or rejects with comments.
5. On approve, a publish job:
   - packages the bundle as an immutable tar.gz
   - uploads to **Azure Blob Storage** at `published/{skill_id}/{version}/bundle.tar.gz`
   - flips status to `approved` in Cosmos with the blob URL + checksum
6. Approved skills are exposed via a **public catalog REST API** that agent runtimes (Hermes etc.) hit to discover and download skills.
7. A **curator** process runs in the background to maintain the published catalog:
   - usage tracking (agent runtimes POST usage events on skill load)
   - deterministic transitions: unused 30 days → stale, unused 90 days → archive
   - LLM review pass proposes consolidations of near-duplicates, flags drift
   - hard invariants: NEVER auto-deletes, pinned skills are immune, snapshot before every real pass, rollback supported

## Architecture — what lives where (final decision after pushback iteration)

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

### Non-negotiable Redis rules
1. Writes ALWAYS hit Cosmos first; Redis invalidation happens after Cosmos write succeeds.
2. Cache misses are normal, not errors. Every Redis read path must have a Cosmos fallback.
3. TTL everything in Redis. No infinite-lived keys.
4. Classifier queue is the only place Redis temporarily holds in-flight data — mitigated by writing the pending doc to Cosmos BEFORE pushing to Redis queue, plus a janitor sweep that re-queues lost messages.

## Tech stack (locked in)
- Backend: **FastAPI** (Python 3.12)
- Frontend: **Next.js 14** + Tailwind
- Database (SoR): Azure Cosmos DB for NoSQL
- Cache + queue: Azure Cache for Redis
- Storage: Azure Blob Storage
- Background workers: Azure Functions (prod), Python worker process (local dev)
- Classifier agent: small aux-model subagent (reuses Hermes-style pattern)
- Auth: Entra ID (OIDC) for humans, API keys for agent runtimes. POC uses header stub.
- Local dev: Cosmos DB emulator + Azurite + redis:7 container (zero Azure spend)
- Infra-as-code: Bicep

## Users
- **Contributor** — anyone in the org. Uploads skills, sees own submissions.
- **Manager** — reviews pending queue, approves/rejects, overrides classifier.
- **Consumer (agent runtime)** — read-only API: list, search, download, report usage.
- **Admin** — manages users, configures curator, runs rollbacks, views audit log.

## In scope (v1 / MVP)
- Web UI: upload form, my-submissions, manager review queue
- Backend API: upload, list, get, download, usage, admin
- Classifier agent (async, queue-backed)
- Manager approval flow → publish to Blob
- Public read-only catalog API for agent runtimes
- Curator: usage tracking + deterministic stale/archive + snapshots + rollback + pinning
- Audit log for all state transitions
- Local dev stack via docker-compose with emulators

## Out of scope (v1)
- In-browser skill editor (upload only; edits = new version)
- Multi-tenant / org isolation
- Public marketplace / external publishing
- Skill execution / sandbox testing in the hub
- SSO production hardening (POC uses header stub)
- Billing, quotas, advanced rate limiting

## Milestones
- **M0 — POC (2 weeks):** Repo scaffolded, end-to-end flow on local emulators (upload → classify → approve → publish → list works)
- **M1 — Azure deployment + auth (+2 weeks):** Bicep templates, Entra ID OIDC, API keys, CI/CD
- **M2 — Curator (+2 weeks):** Usage pipeline, stale/archive transitions, snapshots, rollback, pinning
- **M3 — Curator LLM review (+1 week):** Aux-model review pass, consolidation suggestions surfaced in manager UI
- **M4 — Hardening (ongoing):** Rate limiting, observability, runbooks

## Data model — Cosmos containers
1. `skills` (partition key `/skill_id`) — one doc per skill version
2. `audit` (partition key `/skill_id`) — append-only state transition log
3. `usage_events` (partition key `/skill_id`, TTL 90 days) — raw usage events

See `docs/PRD.md` in this repo for the full draft PRD with field-level schemas.

## Success metrics
- Skills published in first 90 days
- % of agent runtimes pulling from hub weekly
- Time-from-upload to-approval (target <48h p50)
- Classifier accuracy (manager override rate <30%)
- Zero accidental deletions (hard invariant)

## Open questions (PRD should preserve these for Michael to answer later)
1. Skill taxonomy: adopt Hermes categories or design custom?
2. Versioning: enforce semver or freeform? Auto-bump on approval?
3. Per-skill ownership: only original uploader can submit new versions?
4. Duplicate handling: hard-block on classifier dup flag, or warn-only?
5. Draft state visible only to uploader before submission?
6. Beyond schema validation, any automated skill checks (referenced tools exist, etc.)?
7. Curator LLM budget: cap skills per review run?

## Risks (preserve in PRD)
- Classifier mis-categorizes at scale → manager can override, classifier output is suggestion
- Curator archives skill someone needed → 30/90 day grace, pinning, snapshots, never deletes
- Cosmos costs balloon with usage events → TTL on usage_events, aggregated counters on skill doc
- Manager review becomes bottleneck → quality-score sorting, eventual trusted-uploader auto-approve
- Skill bundle contains secrets / malicious payloads → pre-publish scan, manager review is gate

## Existing artifacts in this repo
- `docs/PRD.md` — Hermes-drafted PRD v0.1 (already detailed, but written in Hermes's format)
- `README.md` — short overview

The `/create-prd` workflow should regenerate the PRD using the opencode-agent-builder template structure in `.opencode/commands/create-prd.md`, writing to `docs/PRD.md` (overwrite). Treat the existing PRD.md as additional context — preserve every architectural decision, especially the Cosmos+Redis+Blob storage split and the four Redis rules.
