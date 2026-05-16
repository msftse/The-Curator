# Feature: M4 — AKS Deployment (Helm Umbrella Chart, Four Images, AGIC + Workload Identity)

The following plan is the implementation contract for M4 of the Agentic Skill Hub. It moves the runtime from Azure App Service + Static Web Apps to **Azure Kubernetes Service**, while preserving every non-negotiable invariant from AGENTS.md (§3 storage split, §4 four Redis rules, §5 never-delete, §6 local-first dev loop, §6a auth modes).

**The Python codebase does not change.** This is a packaging + deployment-topology change. `backend/app.py`, `backend/workers/classifier.py`, and `backend/workers/curator_scheduler.py` keep their current entrypoints. What changes: four Dockerfiles, one Helm umbrella chart, an `infra/modules/aks.bicep` module + `infra/modules/acr.bicep` module, and a hard switch in `infra/main.bicep` that replaces `appservice.bicep` / `worker.bicep` / `staticwebapp.bicep` with the AKS module.

Pay special attention to:

- **AGENTS.md §4 rule #4 — classifier queue is the one ephemeral data location.** AKS makes this *more* important, not less. The classifier Deployment is KEDA-scaled from `0..N` based on `LLEN queue:classifier`. When KEDA scales the Deployment to zero, no consumer is BLPOP-ing. The mitigation chain from AGENTS.md §4 still holds: Redis AOF on `queue:classifier`, pending Cosmos doc written *before* `LPUSH`, and the existing janitor sweep (`backend/services/janitor.py`) re-queues `classifier_status=pending` docs older than threshold. KEDA pollingInterval becomes a tunable for upload→classify latency.
- **AGENTS.md §5 — never-delete invariant.** The curator runs as a K8s `CronJob` with `concurrencyPolicy: Forbid`. That is the *second* line of defense. The *first* is still the existing Redis lock (`key_curator_run_lock()` in `backend/core/redis.py`). If you ever remove the Redis lock because "the CronJob already prevents concurrency," you have broken the invariant for the API-triggered on-demand path (Task 9). Both layers must remain.
- **AGENTS.md §6 — local-first dev loop is untouched.** `docker compose up -d` + `make api` + `make worker` + `make web` continues to work identically. AKS is a deploy target only. The four Dockerfiles MUST build and run against the existing emulator stack (Cosmos emulator + Azurite + redis:7) with no Kubernetes anywhere in the loop.
- **AGENTS.md §6a — auth modes unchanged.** Pods read `AUTH_MODE` from a mounted Key Vault secret. `LOCAL_DEV` is never `1` in cluster. The frontend image is **environment-agnostic** — a single image promoted across dev/staging/prod. Frontend env (`AUTH_MODE`, `API_BASE`, Entra IDs) is injected at pod start via a `/env.js` route that emits `window.__ENV__ = {...}` from `process.env` (read by the Node server at request time). Client code reads `window.__ENV__` instead of `process.env.NEXT_PUBLIC_*`. See Task 4 + Task 4a for the implementation. This means MSAL initialization is deferred until `window.__ENV__` is populated.
- **Workload Identity, not pod identity.** Each Deployment (and the CronJob) has its own `ServiceAccount` annotated with `azure.workload.identity/client-id`, federated to a per-component UAMI. No service principal credentials in cluster, no `imagePullSecret` for ACR (cluster-scoped `AcrPull` on the kubelet identity), no secrets mounted from values.yaml.

## Feature Description

The Agentic Skill Hub today runs as:

- **Frontend:** Azure Static Web Apps (`infra/modules/staticwebapp.bicep`) — Next.js, MSAL on the client.
- **API:** Azure App Service (`infra/modules/appservice.bicep`) — FastAPI, managed-identity → Key Vault → Cosmos/Redis/Blob.
- **Workers:** A second Azure App Service `worker` site (`infra/modules/worker.bicep`) running both `backend.workers.classifier` and `backend.workers.curator_scheduler` in one process group on the same plan.

This works for M1's POC scale but has three real problems that AKS solves:

1. **The classifier and the curator share a process group.** A classifier OOM (large SKILL.md, model regression) takes the curator down with it. Scaling signals are different (queue depth vs. cron tick) but the App Service plan can only scale on CPU/memory. Wasteful and unsafe.
2. **No scale-to-zero on the classifier.** App Service Always-On keeps a worker hot 24/7 even when nobody has uploaded a skill in eight hours. Burns ~$40/mo per non-prod env on idle Python processes.
3. **The curator's "scheduled" component is a Python `while True: sleep(N)` loop running in `backend/workers/curator_scheduler.py`.** That is not how Kubernetes (or anyone serious) does cron. It's a leftover from M0 when App Service was the only target. K8s CronJob is the native fit.

This milestone delivers:

- **Four images, four roles** — `frontend`, `backend` (API), `classifier` (worker, KEDA-scaled), `curator` (worker, CronJob).
- **One Helm umbrella chart** `charts/agentic-skill-hub/` with per-component sub-templates and per-env `values-{dev,staging,prod}.yaml` overlays.
- **Cluster + registry via Bicep** — `infra/modules/aks.bicep` (cluster, system pool, user pool, OIDC issuer, workload identity, AGIC add-on, Key Vault CSI driver add-on) and `infra/modules/acr.bicep` (registry, AcrPull on kubelet identity). `infra/main.bicep` deletes its references to `appservice.bicep`, `worker.bicep`, `staticwebapp.bicep` and wires in `aks` + `acr` instead.
- **KEDA scaler** on the classifier Deployment, watching `LLEN queue:classifier`.
- **CronJob + on-demand Job** for the curator. The API's existing `POST /v1/admin/curator/run` endpoint, today implemented as a direct in-process call in `backend/api/curator.py`, gains a *production-mode* branch that creates a Job from the CronJob template via the K8s API. The admin role gate (Entra group, AGENTS.md §6a) is unchanged.
- **AGIC ingress** with TLS terminated at App Gateway, WAF v2 in prod, internal vnet integration.
- **GitHub Actions** build + push + `helm upgrade --install` per env, gated by the existing federated credential flow (`scripts/setup_federated_credentials.sh`).
- **Zero change to local dev.** `docker-compose.yml`, the Makefile, `scripts/wait_for_emulators.py`, and the unit/integration test suites are untouched.

The four image trigger model — explicitly confirmed because the prior conversation surfaced ambiguity:

| Image | K8s shape | Trigger |
|---|---|---|
| `frontend` | Deployment + Service + AGIC ingress | HTTP from users |
| `backend` | Deployment + Service + AGIC ingress | HTTP from frontend + API-key consumers |
| `classifier` | Deployment, replicas managed by **KEDA ScaledObject** | Backend `LPUSH queue:classifier` → KEDA observes queue depth → scales Deployment → worker `BLPOP`s |
| `curator` | **CronJob** (`concurrencyPolicy: Forbid`) + ad-hoc `Job` created by the backend on `/v1/admin/curator/run` | Cron schedule + admin click |

The backend never calls the K8s API for the classifier. The backend calls the K8s API only for the curator on-demand path, with narrowly-scoped RBAC (`create jobs` on the `curator-ondemand` template only, in the `skillhub` namespace only).

## User Story

As a **platform operator** I want the API, the classifier, and the curator to fail independently, scale independently, and deploy independently so a classifier hotfix at 2am doesn't risk the public catalog API SLO.

As a **finance owner** I want the classifier to scale to zero replicas when the queue is empty so non-prod environments cost less than $50/mo when nobody is uploading skills.

As an **SRE** I want the curator to run as a native K8s CronJob — observable in `kubectl get cronjob` and `kubectl get jobs`, alertable via Container Insights — instead of a Python `while True: sleep(86400)` loop hidden inside an App Service.

As a **security reviewer** I want every pod to authenticate to Azure via federated workload identity, with zero stored credentials in cluster, and zero `imagePullSecret` for ACR.

As a **contributor running locally** I want `make api && make worker && make web` to keep working exactly as it does today; AKS must be invisible to local development.

## Problem Statement

The M1 App Service deployment has three structural issues this plan resolves:

1. **Process co-location of classifier + curator.** `infra/modules/worker.bicep` deploys a single App Service that runs both `backend.workers.classifier` and `backend.workers.curator_scheduler`. They have unrelated failure modes, unrelated scaling signals, and unrelated resource profiles. A single OOM or import error takes both down. **Resolution:** Two separate K8s workloads — a Deployment (classifier) and a CronJob (curator) — with independent images and independent identities.

2. **No queue-aware scaling.** App Service Autoscale rules can only fire on CPU, memory, or HTTP queue length. The classifier's actual load signal is `LLEN queue:classifier` in Redis. The only way to use this on App Service is a custom metric pipeline (App Insights custom metric → Autoscale on custom metric), which is brittle and adds two extra hops. **Resolution:** KEDA Redis scaler reads `LLEN` directly with sub-second polling and scales the Deployment between `minReplicaCount: 0` and `maxReplicaCount: 10` (configurable per env).

3. **In-process cron.** `backend/workers/curator_scheduler.py` implements its own scheduler loop. This is well-tested but is the wrong primitive for a K8s world: the loop can drift, can be killed mid-tick by a pod recycle, and is invisible to standard alerting. **Resolution:** K8s `CronJob` with `concurrencyPolicy: Forbid` is the *schedule owner*. The Python entrypoint is invoked with `--once` and runs to completion. The existing `backend/services/curator.py` business logic is unchanged. The existing Redis lock (`key_curator_run_lock()`) is still acquired inside the run — belt and suspenders.

Non-issues that this plan deliberately does **not** solve:

- **It does not change auth.** Entra OIDC, MSAL on the frontend, API keys for agents — all unchanged.
- **It does not change the storage split.** Cosmos / Redis / Blob roles per AGENTS.md §3 are unchanged. The same three clients in `backend/core/{cosmos,redis,blob}.py` connect to the same three Azure resources.
- **It does not move data.** No Cosmos containers added, no migrations. The data plane (`infra/modules/cosmos.bicep`, `infra/modules/storage.bicep`, `infra/modules/redis.bicep`) is unchanged.

## Goals & Non-Goals

### Goals

- Cleanly replace App Service + SWA with AKS in `infra/main.bicep`. Old modules deleted, not coexisting. One runtime target per env.
- Four Dockerfiles, each building from a single `uv`-managed Python project where applicable. The frontend image is a separate Node toolchain.
- One Helm umbrella chart at `charts/agentic-skill-hub/`, per-env values overlays in `charts/agentic-skill-hub/values-{dev,staging,prod}.yaml`. One `helm upgrade --install` call deploys the entire app.
- Per-component `ServiceAccount` + UAMI binding for workload identity. Secrets read from Key Vault via the CSI driver. Zero secret material in `values.yaml`.
- KEDA Redis scaler on the classifier, configurable thresholds per env.
- CronJob curator with `Forbid` concurrency. Ad-hoc Job creation from the backend gated by admin role + narrow RBAC.
- AGIC ingress with TLS terminated at App Gateway. WAF v2 enabled in prod. Frontend and backend share one App Gateway, distinguished by host or path.
- GitHub Actions workflow `deploy-aks.yml` that builds + pushes the four images and runs `helm upgrade --install` per env. Existing federated credentials reused; no new service principals.
- Zero change to local dev. `make` targets, `docker-compose.yml`, integration tests against emulators all unchanged.
- Existing unit + integration test suites pass without modification.

### Non-Goals

- **No multi-cluster / multi-region.** Single cluster per env. Multi-region is post-M4.
- **No service mesh.** No Istio, no Linkerd, no mTLS-everywhere. AGIC + AKS NetworkPolicy is enough at this scale.
- **No GitOps tool (Flux/Argo).** Helm + GitHub Actions push model. GitOps is a worthwhile follow-up but adds tool surface and a learning curve we don't need on the critical path.
- **No replacement of `backend/workers/curator_scheduler.py`'s business logic.** It gains a `--once` flag and is invoked by the CronJob; the in-process loop becomes a single-shot run. Nothing in `backend/services/curator.py` or `backend/services/curator_review*.py` changes.
- **No K8s job for the classifier.** Classifier is a long-running Deployment. KEDA scales replicas. A per-message Job pattern is theoretically cleaner but adds Redis-message-to-K8s-Job glue we don't need.
- **No introduction of OpenAI or Anthropic LLM clients.** Foundry remains the sole aux-model provider per M3.
- **No changes to the four Redis rules.** This plan is enforced against AGENTS.md §4 in Task 11's review checklist.
- **No removal of the existing App Service Bicep modules from disk.** They're deleted from `main.bicep`'s composition but the files remain in `infra/modules/` for one milestone as a rollback escape hatch. Removed in M5.

## Acceptance Criteria

### Build

- [ ] `Dockerfile.backend` builds a runnable image: `docker run --rm -e AUTH_MODE=stub -e LOCAL_DEV=1 -p 8000:8000 skillhub-backend` serves `GET /health` returning `200`.
- [ ] `Dockerfile.classifier` builds a runnable image: `docker run --rm -e AUTH_MODE=stub -e LOCAL_DEV=1 --network host skillhub-classifier` connects to the local Redis emulator and BLPOPs `queue:classifier` (verify via logs).
- [ ] `Dockerfile.curator` builds a runnable image: `docker run --rm -e AUTH_MODE=stub -e LOCAL_DEV=1 --network host skillhub-curator python -m backend.workers.curator_scheduler --once --dry-run` produces a dry-run report and exits `0`.
- [ ] `Dockerfile.frontend` builds a runnable image: `docker run --rm -e NEXT_PUBLIC_AUTH_MODE=stub -e NEXT_PUBLIC_API_BASE=http://localhost:8000 -p 3000:3000 skillhub-frontend` serves the upload page at `http://localhost:3000`.
- [ ] All four images build under 90s on a warm Docker cache.
- [ ] All four images use multi-stage builds with a non-root final user. `docker scout cves` reports zero high/critical CVEs in the base layers at build time.

### Infra

- [ ] `infra/modules/aks.bicep` provisions: AKS cluster (Kubernetes ≥1.29), system node pool (2× Standard_D2s_v5), user node pool (autoscale 1..5× Standard_D4s_v5), workload identity OIDC issuer enabled, AGIC add-on, Key Vault CSI driver add-on, Container Insights add-on.
- [ ] `infra/modules/acr.bicep` provisions a Premium ACR with AcrPull granted to the AKS kubelet identity. No imagePullSecret needed in cluster.
- [ ] `infra/main.bicep` no longer references `appservice.bicep`, `worker.bicep`, or `staticwebapp.bicep` in its module graph. `az deployment group what-if` against a fresh resource group shows: cluster, ACR, Cosmos, Redis, Storage, Key Vault, App Insights — no App Service, no Static Web App.
- [ ] Four UAMIs created (`frontend`, `backend`, `classifier`, `curator`), each federated to a per-component K8s ServiceAccount. RBAC role assignments target each UAMI individually (Key Vault Secrets User, Cosmos DB Data Contributor, Storage Blob Data Contributor as appropriate per AGENTS.md §3).
- [ ] One additional UAMI (`backend-k8s-jobs`) federated to the backend ServiceAccount, with a K8s Role granting `create jobs` on the `curator-ondemand` CronJob *only*.

### Helm

- [ ] `charts/agentic-skill-hub/Chart.yaml` declares the umbrella chart. No subchart dependencies — sub-templates live in `templates/` subdirectories per component.
- [ ] `helm lint charts/agentic-skill-hub --values charts/agentic-skill-hub/values-dev.yaml` passes for all three env value files.
- [ ] `helm template charts/agentic-skill-hub --values charts/agentic-skill-hub/values-dev.yaml | kubectl apply --dry-run=client -f -` succeeds against an empty cluster.
- [ ] All four Deployments / the CronJob declare `automountServiceAccountToken: true` (workload identity requirement), `securityContext.runAsNonRoot: true`, resource `requests`/`limits` set per env, `livenessProbe` + `readinessProbe` for the two HTTP services.

### Runtime — staging cluster smoke test

- [ ] `helm upgrade --install skillhub charts/agentic-skill-hub -f values-staging.yaml` returns `STATUS: deployed`.
- [ ] `kubectl get pods -n skillhub` shows: 2× backend, 2× frontend, 0× classifier (queue empty), 0 active curator pods.
- [ ] Upload a SKILL.md via the AGIC-fronted UI → KEDA scales classifier from 0 → 1 within `pollingInterval` (default 30s, configurable to 5s in staging) → skill flips to `classified` in Cosmos → KEDA scales back to 0 within `cooldownPeriod`.
- [ ] `kubectl get cronjob -n skillhub` shows `curator-scheduled` with the configured schedule. Manual `kubectl create job --from=cronjob/curator-scheduled curator-manual-1` completes with exit code 0 and produces a dry-run report blob in the snapshots container.
- [ ] `POST /v1/admin/curator/run` from an admin Entra account creates a Job via the K8s API, the Job completes, and the response includes the Job name. RBAC check: a non-admin token returns 403 *before* any K8s API call (AGENTS.md §6a authz gate is upstream of the K8s call).
- [ ] Soft-kill the Redis StatefulSet (`kubectl rollout restart`) → the API stays healthy (Cosmos fallback per AGENTS.md §4 rule 2), classifier pauses on BLPOP and recovers when Redis returns, no 5xx storm.
- [ ] `kubectl drain` the user node pool one node at a time → no API 5xx for HTTP traffic > 200ms p95 during the drain window. PodDisruptionBudget honors `minAvailable: 1` for backend and frontend.

### Auth & Security

- [ ] All four pods authenticate to Azure via federated workload identity. `kubectl exec` into the backend, run `az account show`, see the per-component UAMI client ID — no service principal, no managed identity client secret, no env var holding a credential.
- [ ] Key Vault secrets (Cosmos endpoint, Redis password, Foundry endpoint + API key) are mounted via CSI driver at `/mnt/secrets-store/` and read by `backend/core/config.py` via the existing env-var path. Helm chart's `SecretProviderClass` template references the per-env Key Vault.
- [ ] `kubectl get secret -n skillhub` shows only auto-synced TLS secrets and CSI-mirrored secret references. No raw `data:` containing Cosmos keys or Redis passwords.
- [ ] AGIC ingress in prod terminates TLS at App Gateway with a cert from Key Vault (CSI-mounted on the AGIC pod), WAF v2 enabled in `Prevention` mode, OWASP CRS 3.2 rule set. Dev/staging may use HTTP-only or self-signed for first deploy.
- [ ] `NetworkPolicy` resources restrict: classifier pods can only egress to Redis + Cosmos + Blob (no public internet, no other namespaces); curator pods can only egress to Redis + Cosmos + Blob + Foundry; backend pods egress to all four plus Entra (`login.microsoftonline.com`); frontend pods egress only to backend's in-cluster Service.

### Invariants

- [ ] **AGENTS.md §4 rule 4 still holds:** the janitor sweep test in `backend/tests/integration/test_janitor_sweep.py` passes when run against a deployed staging cluster (Redis AOF on, classifier scaled to 0 mid-upload, janitor re-queues within threshold).
- [ ] **AGENTS.md §5 holds:** `backend/tests/unit/test_never_delete_invariant.py` continues to pass without modification. The Helm chart adds no new code paths that could call `delete_item` or `delete_blob`.
- [ ] **Local dev unchanged:** all unit tests pass, all integration tests pass against `docker compose up -d`, `make demo` completes successfully against the local stack with no AKS dependency.

### CI/CD

- [ ] `.github/workflows/deploy-aks.yml` builds + pushes the four images tagged `{git-sha}` and `{env}-latest`, then runs `helm upgrade --install skillhub charts/agentic-skill-hub -f values-{env}.yaml --set image.tag={git-sha} --wait --timeout=10m`.
- [ ] Federated credentials from `scripts/setup_federated_credentials.sh` are reused; no new client secrets.
- [ ] Rollback path is `helm rollback skillhub <revision>` — documented in `infra/README.md`.
- [ ] Existing `.github/workflows/ci.yml` is unchanged. CI does not require a cluster.

### Documentation

- [ ] `infra/README.md` updated with: cluster provisioning steps, ACR push instructions, `kubectl` connection commands per env, Helm install/upgrade/rollback recipes, troubleshooting runbook (pod CrashLoopBackOff, KEDA not scaling, CronJob not firing, AGIC ingress not routing).
- [ ] `AGENTS.md` §3 unchanged (storage split). §4 unchanged (Redis rules). New §13 added: "Runtime topology — AKS." References this plan, lists the four images, lists the workload-identity ServiceAccounts.
- [ ] `README.md` Quickstart section updated to clarify "local dev uses docker-compose; AKS is prod only." No AKS commands added to the contributor on-ramp.

## Files & Locations

### New files

```
charts/
  agentic-skill-hub/
    Chart.yaml                            # umbrella chart metadata
    values.yaml                           # defaults
    values-dev.yaml                       # dev overlay
    values-staging.yaml                   # staging overlay
    values-prod.yaml                      # prod overlay
    templates/
      _helpers.tpl                        # shared label/name/serviceAccount helpers
      namespace.yaml                      # skillhub namespace + ResourceQuota
      frontend/
        deployment.yaml
        service.yaml
        ingress.yaml                      # AGIC ingress for frontend host
        serviceaccount.yaml
        pdb.yaml
        networkpolicy.yaml
      backend/
        deployment.yaml
        service.yaml
        ingress.yaml                      # AGIC ingress for /v1/* paths
        serviceaccount.yaml
        role.yaml                         # `create jobs` on curator-ondemand only
        rolebinding.yaml
        pdb.yaml
        networkpolicy.yaml
        secretproviderclass.yaml          # CSI driver class for backend secrets
      classifier/
        deployment.yaml
        serviceaccount.yaml
        scaledobject.yaml                 # KEDA, watches LLEN queue:classifier
        triggerauthentication.yaml        # KEDA workload identity auth
        networkpolicy.yaml
        secretproviderclass.yaml
      curator/
        cronjob.yaml                      # scheduled curator pass
        job-template-ondemand.yaml        # on-demand Job template (no schedule)
        serviceaccount.yaml
        networkpolicy.yaml
        secretproviderclass.yaml

infra/
  modules/
    aks.bicep                             # cluster + node pools + addons + UAMIs + federated credentials
    acr.bicep                             # registry + AcrPull on kubelet identity

Dockerfile.backend                        # uv-based, multistage, non-root
Dockerfile.classifier                     # uv-based, multistage, non-root
Dockerfile.curator                        # uv-based, multistage, non-root
Dockerfile.frontend                       # node:20-alpine, Next.js standalone output

.dockerignore                             # shared across all four Dockerfiles
.github/
  workflows/
    deploy-aks.yml                        # build + push + helm upgrade
```

### Modified files

```
infra/main.bicep                          # remove appservice/worker/staticwebapp modules; add aks + acr
infra/parameters/{dev,staging,prod}.bicepparam  # AKS-specific params (k8sVersion, nodePoolSizes, etc.)
infra/README.md                           # AKS provisioning + Helm runbook
backend/workers/curator_scheduler.py      # add `--once` flag for CronJob invocation
backend/api/curator.py                    # `/v1/admin/curator/run` gains prod-mode branch that creates a K8s Job
backend/core/config.py                    # add `K8S_CURATOR_CRONJOB_NAME`, `K8S_NAMESPACE` settings (no behavior change in local-dev)
pyproject.toml                            # add `kubernetes` Python client as optional dependency under `[project.optional-dependencies].k8s`
AGENTS.md                                 # new §13 "Runtime topology — AKS"
README.md                                 # Quickstart clarifies local-dev story
```

### Untouched (verify in review)

```
backend/services/                         # no business logic changes
backend/workers/classifier.py             # no changes — runs identically under K8s
backend/tests/                            # all tests unchanged, must pass
docker-compose.yml                        # local dev contract unchanged
Makefile                                  # `make api/worker/web/curator` unchanged
infra/modules/cosmos.bicep                # data plane unchanged
infra/modules/redis.bicep                 # data plane unchanged
infra/modules/storage.bicep               # data plane unchanged
infra/modules/keyvault.bicep              # data plane unchanged
infra/modules/appinsights.bicep           # observability unchanged
infra/modules/rbac.bicep                  # repurposed: now grants roles to AKS UAMIs instead of App Service identities; principalIds list pivots, file structure unchanged
```

## Implementation Tasks

Tasks are sequenced so each phase is independently shippable and reviewable. Within a phase, sub-tasks can parallelize.

### Phase 1 — Images (no cluster, no Helm)

**Task 1 — Backend Dockerfile.**
Multi-stage. Stage 1 (`builder`): `python:3.12-slim` + `uv`, run `uv sync --frozen --no-dev` against the project. Stage 2 (`runtime`): `python:3.12-slim`, copy the venv + `backend/` package, non-root user (`uid=10001`), `EXPOSE 8000`, `CMD ["uvicorn", "backend.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]`. Add `HEALTHCHECK` hitting `/health`. Validate locally against `docker compose up -d` emulator stack.

**Task 2 — Classifier Dockerfile.**
Same builder stage as backend (cache layer reuse). Runtime stage CMD becomes `["python", "-m", "backend.workers.classifier"]`. No port, no HEALTHCHECK (Deployment uses `livenessProbe: exec`-based check via a marker file the worker touches each BLPOP cycle, defined in the K8s manifest). Validate by running against local Redis emulator with a seeded queue message.

**Task 3 — Curator Dockerfile.**
Same builder stage. Runtime CMD `["python", "-m", "backend.workers.curator_scheduler", "--once"]`. Implements Task 8's `--once` flag dependency. Validate via `--dry-run --once` against the local stack.

**Task 4 — Frontend Dockerfile.**
Stage 1 (`deps`): `node:20-alpine`, `pnpm install --frozen-lockfile`. Stage 2 (`builder`): `pnpm build` with **no `NEXT_PUBLIC_*` env vars** — the build is environment-agnostic. Stage 3 (`runtime`): `node:20-alpine`, copy `.next/standalone` + `.next/static` + `public/`, non-root, `EXPOSE 3000`, `CMD ["node", "server.js"]`. Validate: with `AUTH_MODE=stub API_BASE=http://localhost:8000 docker run`, the image serves `/` and `/env.js` returns the injected values. **One image, promotable across all envs.**

**Task 4a — Runtime env injection route.**
New file `frontend/app/env.js/route.ts` exports a `GET` handler that emits `window.__ENV__ = { AUTH_MODE, API_BASE, ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_API_SCOPE }` as JavaScript. `export const dynamic = "force-dynamic"` to bypass static caching. Cache headers: `no-store`. Server reads from `process.env` (no `NEXT_PUBLIC_` prefix needed since this runs server-side at request time).

New file `frontend/lib/env.ts` defines a typed `env` accessor: on the server, reads `process.env`; on the client, reads `window.__ENV__` with a guard for SSR pre-hydration. All client code that today reads `process.env.NEXT_PUBLIC_*` must migrate to `import { env } from "@/lib/env"`. Affected files (grep `NEXT_PUBLIC_` to confirm): `frontend/lib/auth/msal.ts`, `frontend/lib/auth/AuthProvider.tsx`, `frontend/lib/api/client.ts`. MSAL's `PublicClientApplication` construction is deferred until `window.__ENV__` is observed — easiest pattern is to wrap MSAL init in a `useEffect` inside `AuthProvider`.

`frontend/app/layout.tsx` adds `<script src="/env.js" />` in `<head>` *before* any other script. This ensures `window.__ENV__` is populated before client bundles execute.

Local dev impact: `frontend/.env.local` keeps working — Next.js exposes `.env.local` vars to `process.env` server-side, and `/env.js` reads them the same way as in cluster. The `NEXT_PUBLIC_` prefix is dropped from the new vars (`API_BASE` instead of `NEXT_PUBLIC_API_BASE`). The persona-picker stub mode is unchanged.

Acceptance: `curl http://localhost:3000/env.js` returns `window.__ENV__ = {...}` with the env values; browser dev tools show `window.__ENV__` populated before any React code runs.

**Task 5 — `.dockerignore`.**
Single shared `.dockerignore` at repo root excluding `.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `.next`, `frontend/node_modules`, `backend/tests`, `.opencode`, `docs`, `infra`, `charts`. Verify image sizes are under: backend 250MB, classifier 250MB, curator 250MB, frontend 180MB.

**Task 6 — Curator entrypoint flag.**
Modify `backend/workers/curator_scheduler.py` to accept `--once` (runs one pass and exits) and `--dry-run` (no mutations, snapshot still taken — same semantics as existing admin endpoint). Default behaviour without `--once` (the in-process loop) is preserved for App Service compatibility during the M4 rollout window. Removed in M5. Add unit test in `backend/tests/unit/test_curator_scheduler_cli.py` (new file) covering both flags.

### Phase 2 — Bicep (cluster + registry, no app deploy)

**Task 7 — `infra/modules/acr.bicep`.**
Premium SKU ACR (geo-replication available for M5 multi-region). No admin user. `AcrPull` role assignment for the AKS kubelet identity (cluster-scoped, granted in the AKS module as part of `network` integration). Outputs: `acrName`, `acrLoginServer`.

**Task 8 — `infra/modules/aks.bicep`.**
- Cluster: Kubernetes 1.29+, `oidcIssuerProfile.enabled: true`, `workloadIdentity.enabled: true`, `azurePolicy.enabled: false` (M5), `keyVaultSecretsProvider.enabled: true`, `ingressApplicationGateway.enabled: true` (AGIC add-on with greenfield App Gateway provisioned by the add-on for dev/staging; BYO App Gateway for prod).
- System node pool: 2× `Standard_D2s_v5`, `mode: System`, taints `CriticalAddonsOnly=true:NoSchedule`.
- User node pool: autoscale 1..5× `Standard_D4s_v5`, `mode: User`, no taints. KEDA-scaled classifier pods land here.
- Four UAMIs created in the module (`uami-frontend`, `uami-backend`, `uami-classifier`, `uami-curator`), each with `federatedIdentityCredentials` mapping to the cluster's OIDC issuer + the namespace `skillhub` + the per-component ServiceAccount name.
- Outputs: `clusterName`, `clusterFqdn`, `kubeletIdentityObjectId`, `oidcIssuerUrl`, per-UAMI `clientId` + `principalId`.

**Task 9 — `infra/modules/rbac.bicep` pivot.**
Existing module accepts a `principalIds` array. Repurpose: pass the four UAMI principal IDs from `aks.bicep` outputs instead of the App Service identities. Add per-UAMI fine-grained role assignments per AGENTS.md §3 (`Key Vault Secrets User` for all four; `Cosmos DB Built-in Data Contributor` for backend + classifier + curator; `Storage Blob Data Contributor` for backend + curator; `Storage Blob Data Reader` for classifier).

**Task 10 — `infra/main.bicep` swap.**
Remove `module api 'modules/appservice.bicep'`, `module worker 'modules/worker.bicep'`, `module swa 'modules/staticwebapp.bicep'`. Add `module acr 'modules/acr.bicep'` and `module aks 'modules/aks.bicep'`. Outputs change: drop `apiHostname`, `workerSite`, `frontendHostname`; add `clusterName`, `acrLoginServer`, `aksIngressFqdn`. Update `infra/parameters/{dev,staging,prod}.bicepparam` accordingly.

**Task 11 — Backend K8s Job RBAC.**
A fifth UAMI (`uami-backend-k8s-jobs`) is created in `aks.bicep`. It federates to a *second* federated credential on the backend's ServiceAccount and is granted (via an in-cluster RoleBinding declared in the Helm chart, Task 16) the verbs `create,get,list,watch` on `jobs.batch` and `get` on `cronjobs.batch`, both scoped to the `skillhub` namespace. This is the only K8s API access the backend has.

### Phase 3 — Helm chart (templates)

**Task 12 — `Chart.yaml` + `_helpers.tpl` + `values.yaml`.**
Standard helpers: `skillhub.fullname`, `skillhub.labels`, `skillhub.serviceAccountName` (per-component). `values.yaml` defines the default tree with all four images, four ServiceAccounts, KEDA config block, CronJob schedule, AGIC ingress hosts, Key Vault name placeholder, secret keys to mount.

**Task 13 — Frontend templates.**
Deployment (`replicas: {{ .Values.frontend.replicas }}`, default 2; readiness/liveness on `/`; `env:` block populates `AUTH_MODE`, `API_BASE`, `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_API_SCOPE` from values + optionally CSI SecretProviderClass for any sensitive subset), Service (ClusterIP, port 80 → 3000), Ingress (AGIC class, host `{{ .Values.frontend.host }}`, TLS reference if prod), ServiceAccount (workload identity annotation — kept for future Key Vault access even though frontend currently needs none), PDB (`minAvailable: 1`), NetworkPolicy (egress only to backend service). **Same image tag across all envs** — only the env block in values-{env}.yaml differs.

**Task 14 — Backend templates.**
Deployment (replicas 2 default; readiness on `/health`; mounts CSI SecretProviderClass; `env` block includes `AUTH_MODE=oidc`, `LOCAL_DEV=0`, Entra IDs from values, K8s job config), Service, Ingress (AGIC, host + `/v1/*` path), ServiceAccount (annotated with backend UAMI client ID *and* second federated credential for K8s jobs UAMI), Role + RoleBinding for the K8s API job-creation path, PDB, NetworkPolicy, SecretProviderClass referencing the Key Vault keys.

**Task 15 — Classifier templates.**
Deployment (no replicas key — KEDA owns it), readiness/liveness via `exec`-based touch-file probe, ServiceAccount, KEDA ScaledObject (`minReplicaCount: {{ .Values.classifier.minReplicas }}` default 0, `maxReplicaCount: {{ .Values.classifier.maxReplicas }}` default 10, trigger `redis`, `listLength: 1`, `listName: queue:classifier`, `addressFromEnv: REDIS_URL`), TriggerAuthentication (workload-identity auth so KEDA can read Redis via the classifier UAMI), NetworkPolicy.

**Task 16 — Curator templates.**
CronJob `curator-scheduled` (schedule `0 3 * * *` default, `concurrencyPolicy: Forbid`, `successfulJobsHistoryLimit: 3`, `failedJobsHistoryLimit: 3`, `restartPolicy: OnFailure`, command `python -m backend.workers.curator_scheduler --once`). A separate **suspended** CronJob `curator-ondemand` (`suspend: true`, identical podTemplate, schedule placeholder `0 0 1 1 0`) acts as the template the backend clones via `kubectl create job --from=cronjob/curator-ondemand`. ServiceAccount (workload identity), NetworkPolicy (egress to Redis + Cosmos + Blob + Foundry), SecretProviderClass.

**Task 17 — Per-env value overlays.**
- `values-dev.yaml`: `minReplicas: 1` for backend/frontend, classifier KEDA `pollingInterval: 5s`, curator schedule disabled (`enabled: false`), AGIC uses add-on App Gateway, no WAF, HTTP only.
- `values-staging.yaml`: `minReplicas: 2`, classifier `pollingInterval: 10s`, curator schedule `0 3 * * *`, WAF `Detection` mode.
- `values-prod.yaml`: `minReplicas: 3`, classifier `pollingInterval: 15s` (avoid Redis chatter at scale), curator schedule `0 3 * * *`, WAF `Prevention` mode, BYO App Gateway with prod cert from Key Vault.

### Phase 4 — Backend code change

**Task 18 — Curator on-demand path in `backend/api/curator.py`.**
The existing endpoint `POST /v1/admin/curator/run` today imports `backend.services.curator.run_curator` and invokes it in-process. Add a settings-driven branch:

- `settings.runtime_mode == "k8s"` (new setting, default `"inprocess"` for local-dev compatibility) → import `kubernetes` async client, look up CronJob `curator-ondemand` in `settings.k8s_namespace`, create a Job from its `spec.jobTemplate`, name it `curator-ondemand-{utc-iso-compact}`, return `{job_name, dashboard_url}`. Auth gate (admin role via Entra group) unchanged and remains the first check.
- `settings.runtime_mode == "inprocess"` → existing behaviour. Local dev, App Service rollout window, and tests stay on this path.

New unit test `backend/tests/unit/test_curator_run_endpoint_k8s_branch.py` covers both branches with a `FakeK8sClient` injected via FastAPI dependency override. Existing integration tests at `backend/tests/integration/test_curator_endpoints.py` stay on `runtime_mode=inprocess` and pass unchanged.

**Task 19 — `kubernetes` dependency.**
Add to `pyproject.toml` under `[project.optional-dependencies].k8s = ["kubernetes>=29.0.0"]`. Backend Dockerfile installs with the `k8s` extra; local dev does not (`uv sync` without the extra). `backend/api/curator.py` import is lazy inside the `k8s` branch to keep local dev unaffected.

### Phase 5 — CI/CD

**Task 20 — `.github/workflows/deploy-aks.yml`.**
Triggered on push to `main` and on tagged releases. Jobs:
1. `build-and-push` (matrix over the four images): `docker/login-action` to ACR via federated cred, `docker/build-push-action` with `tags: {acr}/skillhub-{component}:{git-sha},{acr}/skillhub-{component}:{env}-latest`.
2. `helm-deploy` (matrix over envs `dev,staging,prod`, prod gated on `release` tags only, staging gated on `main` branch): `azure/setup-helm`, `azure/aks-set-context` via federated cred, `helm upgrade --install skillhub charts/agentic-skill-hub -f charts/agentic-skill-hub/values-{env}.yaml --set image.tag={git-sha} --namespace skillhub --create-namespace --wait --timeout=10m`.
3. `smoke-test` (post-deploy): `curl https://{ingress-host}/health` returns 200; `kubectl wait --for=condition=available deployment/backend deployment/frontend -n skillhub --timeout=300s`; basic upload→classify integration smoke via the demo API key. Failure triggers `helm rollback`.

**Task 21 — Federated credentials extension.**
`scripts/setup_federated_credentials.sh` already grants the GH Actions OIDC token contributor access to the resource group. No change needed for AKS-specific perms; the existing role assignment covers both Bicep deploys and `az aks get-credentials`. Document the verification step in `infra/README.md`.

### Phase 6 — Documentation & sign-off

**Task 22 — `AGENTS.md` §13.**
New section "Runtime topology — AKS." Lists the four images, the four ServiceAccount → UAMI mappings, the KEDA scaler, the CronJob, the on-demand Job RBAC scope, the AGIC ingress topology. References this plan as the implementation contract.

**Task 23 — `infra/README.md` runbook.**
Sections: "Provisioning a new environment" (Bicep deploy, `az aks get-credentials`, `kubectl create namespace`, `helm install`), "Rotating images" (CI does it; manual command for hotfix), "Rolling back" (`helm rollback skillhub <revision>`), "Troubleshooting" (KEDA not scaling: check Redis URL on TriggerAuthentication; CronJob not firing: check timezone + suspend flag; AGIC 502: check NetworkPolicy + readinessProbe; pod stuck Pending: check user node pool autoscaler).

**Task 24 — `README.md` quickstart clarification.**
One paragraph reaffirming AGENTS.md §6 — local dev uses `docker-compose`, AKS is a deploy target, contributors do not need `kubectl`. Link to `infra/README.md` for ops.

**Task 25 — Decommission window for App Service modules.**
`infra/modules/appservice.bicep`, `worker.bicep`, `staticwebapp.bicep` are not referenced after Task 10 but stay on disk. Tag the milestone end with a comment header in each unreferenced module pointing at this plan and noting "removed in M5." Open a placeholder issue for M5 cleanup.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| KEDA scale-from-zero introduces latency spike on first upload after idle | High | Low | Tunable `pollingInterval` and `minReplicaCount`; staging defaults to `0`, prod can run `1` if first-upload latency matters. Documented in Task 23 runbook. |
| Classifier pod killed mid-job during KEDA scale-down | Medium | Low | Existing janitor sweep (AGENTS.md §4 rule 4) re-queues lost messages. Verified in acceptance test. `terminationGracePeriodSeconds: 60` on the classifier Deployment gives the BLPOP loop time to finish the current message. |
| CronJob skipped while previous run still active | Low | Low | `concurrencyPolicy: Forbid` is the explicit policy. K8s emits a `JobAlreadyActive` event. Monitor in Container Insights; alert if skip count > 1/day. Redis lock TTL is the secondary guard. |
| Backend pod gains too much K8s API power via misconfigured Role | Medium | High | RBAC scoped to `create,get` on `jobs.batch` *only* in `skillhub` namespace. Role yaml committed to repo, reviewable. Negative test in Task 18 confirms backend cannot list pods, list secrets, or create deployments. |
| `NEXT_PUBLIC_*` env baked into frontend image causes per-env image proliferation | N/A | N/A | **Resolved by Task 4a runtime env injection.** Single frontend image promoted across envs. Adds ~20ms first-paint cost for the `/env.js` round trip; acceptable for an internal tool. |
| `/env.js` route returns stale env after a Helm value change | Low | Low | `cache-control: no-store` on the route + `dynamic: "force-dynamic"` + pod rolling restart on Helm upgrade picks up new env. CDN in front of the frontend (if added later) must honor `no-store` for this path. |
| MSAL initializes before `window.__ENV__` is populated | Medium | High (broken auth) | `<script src="/env.js" />` is placed in `<head>` *before* the Next.js bundle script tags. MSAL init is wrapped in a `useEffect` inside `AuthProvider` that runs after first paint, by which point `window.__ENV__` is guaranteed populated. Unit test in Task 4a verifies the load order. |
| AGIC add-on App Gateway not flexible enough for prod TLS / WAF tuning | Medium | Medium | Prod uses BYO App Gateway (Task 17). Add-on only used in dev/staging where simplicity wins. |
| Bicep deletion of App Service modules orphans real Azure resources | Medium | High | First deploy to a fresh resource group, not an upgrade. Existing M1 envs are torn down and recreated on AKS. Document the cutover in Task 23. No in-place migration. |
| Local dev breaks because someone reaches for `kubectl` in a service | Medium | High | Task 18's `runtime_mode` setting defaults to `inprocess`. CI lint task asserts no `from kubernetes import` at module top level in `backend/`. Imports must be lazy and gated. |
| KEDA Redis trigger auth fails silently if workload identity mis-configured | Medium | Medium | TriggerAuthentication YAML reviewed in code review; KEDA operator logs surfaced in Container Insights; staging smoke test (Task 17/Task 20) catches this before prod. |
| Curator on-demand Job RBAC drift over time | Low | Medium | Role yaml lives in `charts/agentic-skill-hub/templates/backend/role.yaml` and is template-checked by `helm lint` in CI. Any verb expansion requires a PR review. |
| AGENTS.md §4 rule 1 (Cosmos-first) regresses because of new K8s code path | Low | High | Task 18's K8s code path is *only in the admin curator-run endpoint*. It writes nothing to Redis. It creates a K8s Job, that's it. The actual curator pass inside the Job is `backend/services/curator.py` unchanged — still Cosmos-first. |

## Open Questions

These are answerable during implementation but flagging now:

1. **Should KEDA's minReplicaCount be 0 in prod?** Scale-from-zero saves money but adds first-message latency. Staging proves the latency budget; prod default decided at Task 17 cutover.
2. **Frontend SSR or fully static export?** Today the Next.js app uses SSR (`NEXT_PUBLIC_*` baked, but server components are server-rendered). Containerizing SSR is fine. If we later move to fully static + a CDN, the frontend image goes away and we revisit. Out of scope for M4.
3. **Container Insights vs. Prometheus + Grafana?** Container Insights is the AKS add-on default and is one toggle. Prometheus stack is a separate Helm install and a separate cost. M4 ships with Container Insights; revisit if alerting needs outgrow it.
4. **App Gateway BYO in prod — is one shared App Gateway enough for both frontend and backend, or do we want two?** One is cheaper and AGIC handles multi-host. Two isolates blast radius. Default to one; document the two-Gateway upgrade path.
5. **Where does the `kubernetes` Python client get the in-cluster config?** Standard pattern: `kubernetes.config.load_incluster_config()` when the pod's ServiceAccount token is mounted. Local dev never hits this path (`runtime_mode=inprocess`). Verified by absence of any in-cluster service in `docker-compose.yml`.
