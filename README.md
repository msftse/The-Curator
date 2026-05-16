# Agentic Skill Hub

Internal web platform for submitting, reviewing, publishing, and maintaining reusable agent skills.

**Status:** PRD draft. Not yet implemented.

## Docs

- [PRD](docs/PRD.md) — product requirements, architecture, milestones

## Stack (planned)

- Backend: FastAPI (Python 3.12)
- Frontend: Next.js 14 + Tailwind
- Database (SoR): Azure Cosmos DB for NoSQL
- Cache + queue: Azure Cache for Redis (hot reads, classifier queue, locks)
- Storage: Azure Blob Storage (approved bundles, snapshots)
- Auth: Entra ID (OIDC) + API keys for agent runtimes
- Local dev: Cosmos DB emulator + Azurite + redis:7 container
