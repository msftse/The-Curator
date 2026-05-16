# Agentic Skill Hub — Product Requirements Document

**Status:** Draft v0.1
**Owner:** Michael Liav
**Last updated:** 2026-05-16

---

## 1. Summary

Agentic Skill Hub is an internal web platform for submitting, reviewing, publishing, and maintaining reusable agent skills (SKILL.md bundles compatible with Hermes Agent and similar agentic frameworks).

Anyone in the organization can upload a skill. A classifier agent auto-tags it. A manager approves or rejects it. Approved skills are published to a shared artifact store that any agent runtime can pull from. A curator process maintains the published catalog over time — pruning stale skills, flagging duplicates, archiving the dead — without ever deleting silently.

The hub turns ad-hoc, scattered prompt/procedure knowledge into a governed, queryable, version-tracked library.

---

## 2. Problem

Skills today live in three bad places:
1. Individual laptops (`~/.hermes/skills/`) — invisible to the team
2. Random Notion/Confluence pages — not machine-readable, not versioned, not loadable by an agent
3. Inside one-off prompts copy-pasted across projects — stale within a week

Without a hub:
- Same skill gets re-invented five times in five different shapes
- Quality varies wildly with no review gate
- No way to track which skills are actually used
- Stale skills become liabilities — agents follow outdated instructions

The hub solves: **single source of truth for skills + governance + lifecycle management.**

---

## 3. Goals

**In scope (v1):**
- Web UI for uploading SKILL.md bundles (single file or folder with references/templates/scripts)
- Automated classification (category, tags, quality score) via classifier agent on every upload
- Manager review queue with approve/reject + comments
- Approved skills published to immutable, versioned artifact storage
- Public read-only catalog API for agent runtimes to discover and download skills
- Curator process for the published catalog: usage tracking, stale → archive lifecycle, duplicate detection, snapshot-before-mutate, never auto-delete
- Audit log of all state transitions

**Out of scope (v1):**
- Skill editor in the browser (upload only; edits = new version)
- Multi-tenant / org isolation (single org for v1)
- Public marketplace / external publishing
- Skill execution / sandbox testing in the hub itself
- SSO production hardening (POC uses header-based auth stub)
- Billing, quotas, rate limiting beyond basic abuse prevention

---

## 4. Non-Goals

- This is not a replacement for the Hermes per-user skills directory. Users still keep personal skills locally. The hub is for **shared, sanctioned** skills only.
- This is not a code repository. We do not host the *outputs* skills produce — only the skill definitions themselves.
- This is not an LLM eval platform. Quality scoring is heuristic + lightweight LLM review, not benchmark-driven.

---

## 5. Users

| Role | Description | Primary Actions |
|------|-------------|-----------------|
| Contributor | Anyone in the org | Upload skill, view own submissions, see rejection reasons |
| Manager | Approves/rejects skills | Review queue, approve, reject with comments, override classifier |
| Consumer (agent) | Hermes / other agent runtimes | Read-only API: list, search, download, report usage |
| Admin | Hub operator | Manage users, configure curator, view audit log, run rollbacks |

---

## 6. User Stories

**Contributor**
- As a contributor, I upload a SKILL.md (drag-drop) and get an immediate auto-classification preview so I know how the system understood my skill.
- As a contributor, I can submit additional bundle files (references/, templates/, scripts/) alongside the SKILL.md.
- As a contributor, I see the status of my submission (pending/approved/rejected) and any manager feedback.

**Manager**
- As a manager, I open the review queue and see pending skills sorted by submission time, with the classifier's category/tags/quality score visible.
- As a manager, I can view the full SKILL.md and bundle contents inline.
- As a manager, I can approve in one click or reject with a required reason.
- As a manager, I can override the classifier's category/tags before approving.

**Consumer (agent runtime)**
- As an agent, I can hit `GET /v1/skills?category=devops` and get a list of approved skills with metadata.
- As an agent, I can download a skill bundle as a tar.gz or browse files individually.
- As an agent, I can POST a usage event when I load a skill so the curator has data to work with.

**Admin**
- As an admin, I can pause the curator, review what a curator pass *would* do (dry run), and roll back if a real pass goes wrong.
- As an admin, I can pin a skill so the curator never touches it.

---

## 7. Architecture (High Level)

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Next.js Web UI  │─────│   FastAPI API    │─────│   Cosmos DB      │
│  (contributor +  │     │  (REST + auth)   │     │   (metadata,     │
│   manager views) │     │                  │     │    pending,      │
└──────────────────┘     └────────┬─────────┘     │    audit log)    │
                                  │               └──────────────────┘
                                  │
                         ┌────────┼─────────┐
                         │        │         │
                         ▼        ▼         ▼
              ┌──────────────┐ ┌────────────────┐ ┌─────────────────┐
              │  Classifier  │ │  Blob Storage  │ │    Curator      │
              │  Agent       │ │  (published    │ │  (background    │
              │  (on upload) │ │   bundles,     │ │   maintenance)  │
              └──────────────┘ │   snapshots)   │ └─────────────────┘
                               └────────────────┘
```

### Storage split (deliberate)

- **Cosmos DB (for NoSQL)** — system of record for all metadata: skill ID, version, status, classification, audit trail, usage counters, pinning state, pending submissions awaiting review.
- **Blob Storage** — artifact store for approved bundles only. Each approved skill version becomes an immutable tar.gz at `published/{skill_id}/{version}/bundle.tar.gz`. Snapshots for rollback live under `snapshots/`.

**Why split:** Cosmos for query/filter/index (categories, tags, search, audit), Blob for cheap immutable artifact hosting + CDN-frontable downloads. Single source of truth = Cosmos; Blob is regenerable from Cosmos + the original upload payload.

### Lifecycle of a skill

```
upload → pending (Cosmos) → [classifier runs] → classified (Cosmos)
       → [manager reviews] → approved → [publish job] → published (Blob + Cosmos)
                          → rejected (Cosmos, terminal)

published → active → stale (no usage 30d) → archived (no usage 90d, in Blob .archive/)
         ↳ pinned skills bypass auto-transitions
```

---

## 8. Functional Requirements

### 8.1 Upload
- Accept: single SKILL.md, or .zip/.tar.gz bundle, or multi-file form upload
- Validate: YAML frontmatter parses, required fields present (`name`, `description`), markdown body non-empty
- Reject malformed uploads with clear error before they hit Cosmos
- Max bundle size: 10MB v1

### 8.2 Classifier Agent
- Triggered on every successful upload (async, queue-backed)
- Reads SKILL.md, outputs:
  - `category` (single, from a controlled taxonomy: devops, mlops, productivity, social-media, research, creative, …)
  - `tags` (free-form, max 8)
  - `quality_score` (0–100, heuristic + LLM-assessed: clarity, completeness, has-trigger-conditions, has-pitfalls)
  - `summary` (one sentence)
  - `duplicate_candidates` (list of existing approved skill IDs that look similar)
- Writes results back to the same Cosmos doc
- Failure mode: classification timeout = doc remains pending with `classifier_status=failed`, manager sees raw doc and classifies manually

### 8.3 Review Queue
- Manager view: paginated list of pending skills, sortable by submission time / classifier quality score
- Per-skill detail: rendered markdown preview, file tree for bundles, classifier output (editable), uploader info
- Actions: approve, reject (requires reason), edit classification
- Bulk approve gated behind a checkbox per skill (no "approve all" footgun)

### 8.4 Publish
- On approve: background job packages the bundle as immutable tar.gz, uploads to Blob at versioned path, writes blob URL + checksum into Cosmos, flips status to `approved`
- Versioning: every approval creates a new version. Old versions remain downloadable.
- Idempotent: re-running a publish for the same version is a no-op

### 8.5 Public Catalog API
- `GET /v1/skills` — list approved skills (filterable by category, tag, status)
- `GET /v1/skills/{id}` — metadata for one skill
- `GET /v1/skills/{id}/versions` — version history
- `GET /v1/skills/{id}/download` — signed URL to bundle tar.gz
- `POST /v1/skills/{id}/usage` — agent reports load/use event (auth required)
- All endpoints return JSON, paginate cursor-style, include rate limit headers

### 8.6 Curator
- Runs on a schedule (configurable; default daily off-peak)
- Two phases:
  1. **Deterministic transitions** — usage-based: no loads in 30 days → stale; no loads in 90 days → archive (move blob to `archive/` prefix, flip Cosmos status)
  2. **LLM review pass** — aux-model agent surveys active skills, proposes consolidations of near-duplicates, flags drift (e.g., references to deprecated commands), opens "curator suggestions" tickets for manager review
- Hard invariants:
  - **Never auto-deletes** — worst case is archival, which is recoverable
  - **Pinned skills are immune** to all auto-transitions and curator suggestions
  - **Snapshot before every real pass** — full tar.gz of the published Blob tree, kept N (default 5)
  - Dry-run mode produces report without any mutations
- Admin commands: `pause`, `resume`, `run --dry-run`, `run`, `rollback`, `pin`, `unpin`, `restore`

### 8.7 Audit Log
- Every state transition (upload, classify, approve, reject, publish, archive, pin, restore, rollback) writes an immutable audit record to Cosmos
- Queryable by skill ID, actor, action type, time range
- Retention: indefinite v1

---

## 9. Non-Functional Requirements

| Concern | Target |
|---------|--------|
| Upload latency | <2s for files under 1MB |
| Classifier turnaround | <60s p95 (async, user not blocked) |
| Catalog API latency | <300ms p95 for list/get |
| Bundle download | served via signed URL from Blob/CDN, no app-tier proxy |
| Availability | 99% v1 (single region, no HA) |
| Auth | OIDC (Entra ID) for humans; API keys for agent runtimes. POC uses header stub. |
| Audit immutability | Cosmos append-only collection, no update/delete on audit docs |
| Backup | Cosmos continuous backup enabled; Blob snapshots before every curator pass |

---

## 10. Data Model (Cosmos DB — NoSQL API)

### Container: `skills` (partition key: `/skill_id`)
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

### Container: `audit` (partition key: `/skill_id`)
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

### Container: `usage_events` (partition key: `/skill_id`, TTL: 90 days)
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

## 11. Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | FastAPI (Python 3.12) | Matches Hermes ecosystem, reuse skill validators, fast iteration |
| Frontend | Next.js 14 + Tailwind | Solid defaults, SSR for SEO-free internal tool is fine, easy auth |
| Database | Azure Cosmos DB for NoSQL | User-specified, fits JSON document model + global indexing |
| Object Storage | Azure Blob Storage | Cheap immutable artifacts, signed URLs, CDN-frontable |
| Background jobs | Azure Functions (prod), Python worker process (local dev) | Async classifier + publish + curator |
| Classifier agent | Reuses Hermes subagent pattern (small aux model) | Consistent with org's existing agent infra |
| Auth | Entra ID (OIDC) for humans, API keys for agents | Standard Azure stack |
| Local dev | Cosmos DB emulator + Azurite | Zero Azure spend for POC |
| Infra-as-code | Bicep (Azure native) | First-class Azure support |

---

## 12. Milestones

### M0 — POC (target: 2 weeks)
- Repo scaffolded, ARCHITECTURE.md + this PRD committed
- Backend: upload → Cosmos pending → classifier runs → status updates
- Frontend: upload form + my-submissions view + manager review queue
- Approve flow: writes tar.gz to Azurite, flips status
- Public list/download API
- Runs entirely on local emulators (Cosmos + Azurite), no Azure spend

### M1 — Azure deployment + auth (target: +2 weeks)
- Bicep templates for Cosmos, Blob, Functions, App Service
- Entra ID OIDC integration
- API key issuance for agent runtimes
- CI/CD via GitHub Actions

### M2 — Curator (target: +2 weeks)
- Usage tracking pipeline (POST /usage → counters → 30d rolling)
- Deterministic stale/archive transitions on schedule
- Snapshot + rollback
- Pinning + admin commands

### M3 — Curator LLM review (target: +1 week)
- Aux-model review pass with consolidation suggestions
- Suggestions surface in manager UI as actionable items

### M4 — Hardening (ongoing)
- Rate limiting, abuse prevention, observability (App Insights), runbooks

---

## 13. Open Questions

1. **Skill taxonomy** — do we adopt the Hermes categories as-is (devops, mlops, productivity, …) or design a custom one for the org? *Default: Hermes categories.*
2. **Versioning semantics** — semver enforced, or freeform string? Auto-bump on every approval, or contributor-declared? *Default: semver, auto-bump patch on each approval unless contributor specifies.*
3. **Per-skill ownership** — should only the original uploader (or designated owners) be allowed to submit new versions of an existing skill? *Default: yes, with admin override.*
4. **Duplicate handling** — when classifier flags duplicates, hard-block or just warn? *Default: warn, manager decides.*
5. **Public vs private skills** — do we need a private/draft state visible only to uploader before submitting for review? *Default: no in v1; can save draft client-side.*
6. **Skill testing** — any automated checks beyond schema validation (e.g., does the skill reference tools that exist, are commands syntactically valid)? *Default: schema only in v1; deeper validation later.*
7. **Curator LLM cost** — what's the budget for the review pass? Cap at N skills per run? *Default: cap at 50 skills per run, manager-configurable.*

---

## 14. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Classifier mis-categorizes at scale | Medium | Low | Manager can override; classifier output is suggestion not authority |
| Curator archives a skill someone needed | Low | Medium | 30/90 day grace, pinning, snapshot+rollback, never auto-deletes |
| Cosmos costs balloon with usage events | Medium | Medium | TTL on usage_events container (90d), aggregate counters live on skill doc |
| Manager review becomes bottleneck | High | Medium | Quality score sorting, bulk-approve UI, eventually trusted-uploader auto-approve |
| Storage account becomes single point of failure | Low | High | GRS replication, snapshots, Cosmos is system of record so Blob is regenerable |
| Skill bundle contains secrets / malicious payloads | Medium | High | Pre-publish scan (gitleaks-style), manager review is the gate, never auto-execute |

---

## 15. Success Metrics

- Skills published in first 90 days
- % of org running agent runtimes that pull from the hub at least weekly
- Time-from-upload to-approval (target: <48h p50)
- Classifier accuracy (manager-override rate, target <30%)
- Skills archived by curator vs restored (high restore rate = curator too aggressive)
- Zero accidental deletions (hard invariant — measured, not goaled)
