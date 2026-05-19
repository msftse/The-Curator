# Contributing to Agentic Skill Hub

Thanks for contributing. This repo has strong conventions — most of them exist because
violating them silently corrupts data. Read this file end-to-end before opening your
first PR.

Authoritative context lives in [`AGENTS.md`](AGENTS.md) and [`docs/PRD.md`](docs/PRD.md).
This file is the human-facing summary of those.

---

## TL;DR

1. Fork or branch from `main`.
2. Run the local stack: `docker compose up -d`.
3. Make your change. Add tests. Keep it demoable on the local emulator stack.
4. Run lint, format, type-check, and tests locally before pushing.
5. Open a PR using the template. Link the relevant section of `AGENTS.md` or `docs/PRD.md`.

---

## 1. Ground rules (non-negotiable)

These will get a PR rejected on sight. They are spelled out in `AGENTS.md`; this
is the short version.

- **Cosmos-first writes.** Cosmos is the system of record. Redis is cache + ephemeral
  coordination only. Never write to Redis as the only copy. See `AGENTS.md` §4.
- **TTL everything in Redis.** No infinite-lived keys.
- **Cache misses are normal.** Every Redis read path needs a Cosmos fallback.
- **Never delete skill data.** The only allowed `delete_blob` callsite is
  `move_published_to_archive` in `backend/services/curator.py`, and it must verify
  the destination exists first. See `AGENTS.md` §5. There is a static AST test
  (`backend/tests/unit/test_never_delete_invariant.py`) that will fail your PR if you
  add any other delete call near skills or bundles.
- **Pinned skills are immune** to every auto-transition and every curator suggestion.
- **Every state transition writes to the `audit` container.** No exceptions.
- **No secrets in commits.** Use env vars / Key Vault.

If you are about to add a delete, or a Redis write without a Cosmos write, or a
state transition without an audit record — stop and re-read `AGENTS.md`.

---

## 2. Dev setup

### Prerequisites

- Python 3.12
- Node 20+
- Docker (for the emulator stack)
- `uv` for Python dependency management
- `pnpm` for the frontend

### Bring up the local stack

```bash
docker compose up -d   # Cosmos emulator + Azurite + redis:7
cp .env.example .env.local   # if not already present
uv sync
pnpm --filter frontend install
```

### Run it

```bash
# Backend
uv run uvicorn backend.app:app --reload

# Frontend (separate terminal)
pnpm --filter frontend dev

# Classifier worker (separate terminal)
uv run python -m backend.workers.classifier
```

Auth in local dev defaults to `AUTH_MODE=stub` — pass your identity via the
`X-User-Email` header. See `AGENTS.md` §6a for the full auth contract and how to
flip to real Entra OIDC locally.

---

## 3. Workflow

1. **Branch from `main`.** Use a short, descriptive name (`feat/curator-rollback`,
   `fix/classifier-requeue`, `docs/contributing`).
2. **Keep PRs small and focused.** One logical change per PR. If you find yourself
   refactoring while fixing a bug, do the refactor in a separate PR.
3. **Write tests with the code, not after.** See §5.
4. **Update docs in the same PR.** If you change behavior described in `AGENTS.md`,
   `docs/PRD.md`, or `docs/ARCHITECTURE.md`, update it. Stale docs are worse than no
   docs.
5. **Open a PR using the template.** Fill in every section. "N/A" is a valid
   answer; deleting the section is not.

---

## 4. Code style

### Python (backend)

- Python 3.12, type hints on public functions.
- Format: `uv run ruff format`
- Lint: `uv run ruff check`
- Async I/O end-to-end. Do not block the event loop.
- Pydantic models for requests/responses and Cosmos docs.
- Storage clients (Cosmos, Redis, Blob) are injected via FastAPI `Depends`. Never
  instantiate them inside business logic.
- Route modules in `backend/api/` stay thin. Business logic lives in
  `backend/services/`.

### TypeScript (frontend)

- Strict mode on. No `any` without an inline comment justifying it.
- Server Components by default; `"use client"` only when needed.
- Tailwind utility classes; avoid bespoke CSS.
- API calls go through the typed client in `frontend/lib/api/`.

---

## 5. Testing

Every change ships with tests. The bar:

- **Unit tests** for services and pure functions.
- **Integration tests** for anything touching Cosmos, Redis, or Blob — run against
  the local emulator stack.
- **At least one end-to-end happy-path test** per user-facing flow.
- **Audit assertions.** Any new state transition needs a test that verifies the
  `audit` record was written.
- **Curator changes** need a dry-run-vs-real diff test and a snapshot/rollback
  round-trip test.

Run the suite:

```bash
uv run pytest
pnpm --filter frontend test
```

If your change can only be verified against real Azure, it is not M0/M1-ready —
push back on the design.

---

## 6. Pre-commit checklist

Before pushing:

- [ ] `uv run ruff format` clean
- [ ] `uv run ruff check` clean
- [ ] `pnpm --filter frontend lint` clean
- [ ] `pnpm --filter frontend typecheck` clean
- [ ] `uv run pytest` passes
- [ ] `pnpm --filter frontend test` passes
- [ ] Local stack demo works: `docker compose up -d` → user-visible flow runs end to end
- [ ] No secrets in the diff
- [ ] Docs updated if behavior changed
- [ ] Pre-commit hooks pass (do not use `--no-verify`)

CI enforces all of the above starting at M1. Local discipline keeps CI green.

---

## 7. Commit messages

Conventional Commits-ish, lowercase, imperative:

```
feat(curator): add rollback round-trip verification
fix(classifier): re-queue pending docs older than 5m
docs(agents): clarify never-delete invariant scope
test(audit): assert audit row on pin/unpin transitions
```

Reference the section of `AGENTS.md` or `docs/PRD.md` your change relates to in
the PR description, not the commit message.

---

## 8. Reporting bugs and requesting features

Use the issue templates in `.github/ISSUE_TEMPLATE/`:

- **Bug report** — something is broken or behaving incorrectly.
- **Feature request** — propose a change in behavior or new capability.
- **Invariant violation** — for suspected violations of the Cosmos-first, TTL,
  never-delete, or pinned-immune rules. These are triaged first.

For security issues, do **not** open a public issue. Email the maintainers
directly (see repo settings for the security contact).

---

## 9. When in doubt

- Architecture question → `docs/PRD.md` §6
- Why a decision was made → `.opencode/CONTEXT.md`
- Storage placement question → `AGENTS.md` §3 + §4
- About to write a delete? → `AGENTS.md` §5. Don't.
- About to write to Redis without Cosmos? → `AGENTS.md` §4. Don't.

If still unclear, open a draft PR or a discussion issue and ask. It is cheaper to
ask than to re-do a merged change.
