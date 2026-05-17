# Infra — Agentic Skill Hub

Bicep modules describing the Azure footprint for `dev`, `staging`, and `prod`.
**M4+ topology: AKS + ACR + Workload Identity.** App Service / Static Web
App modules were removed in M4 (see `.agents/plans/m4-aks-deployment.md`).

## Layout

```
infra/
├── main.bicep                 # top-level composition (one deployment per env)
├── modules/
│   ├── acr.bicep              # Azure Container Registry (Premium in prod)
│   ├── aks.bicep              # AKS cluster: CNI Overlay + Cilium + WI + OIDC + AGIC
│   ├── identity.bicep         # 5 User-Assigned MIs + federated creds → SAs in skillhub ns
│   ├── rbac.bicep             # role assignments (KV, Cosmos data plane, Blob, ACR pull)
│   ├── cosmos.bicep           # Cosmos NoSQL account + 4 containers
│   ├── storage.bicep          # Storage account + 4 blob containers
│   ├── redis.bicep            # Azure Cache for Redis (AOF on queue in prod)
│   ├── keyvault.bicep         # Key Vault (RBAC) + seeded secret names
│   └── appinsights.bicep      # workspace-based App Insights + Log Analytics
└── parameters/
    ├── dev.bicepparam
    ├── staging.bicepparam
    └── prod.bicepparam
```

Compute lives in AKS; storage modules (`cosmos`, `redis`, `storage`,
`keyvault`) are unchanged from M2/M3 and still govern AGENTS.md §3.

## Naming convention

```
<resource-short>-skillhub-<env>-<region>
```

Examples: `aks-skillhub-prod-eastus`, `kv-skillhub-prod-eastus`,
`cosmos-skillhub-dev-eastus`. Storage account names strip dashes and
truncate to 24 chars.

UAMI names: `id-skillhub-<env>-{frontend,backend,classifier,curator,backend-k8s-jobs}`.

---

## Bootstrap a new environment

Run **once per environment**. The deploy workflow takes over from step 6.

1. **Create the resource group**:

   ```bash
   az group create -n rg-skillhub-dev -l eastus
   ```

2. **Create the Entra App Registrations + admin group** (idempotent):

   ```bash
   scripts/setup-entra.sh <env> <frontend-hostname>
   # e.g. scripts/setup-entra.sh dev skillhub-dev.example.com
   ```

   Provisions:
   - Backend API reg `skillhub-api-<env>` exposing `access_as_user`,
     identifier `api://<api-app-id>`, group claims as `SecurityGroup`.
   - Frontend SPA reg `skillhub-spa-<env>` with SPA redirect URIs
     `https://<frontend-host>/auth/callback` + the localhost equivalent,
     pre-authorized for the backend scope.
   - Security group `skillhub-admins-<env>` — admin role source.

   Script prints a copy-paste block with the IDs for
   `infra/parameters/<env>.bicepparam`.

3. **Set up GitHub federated credentials** so the deploy workflow can
   `az login` without a stored client secret:

   ```bash
   scripts/setup_federated_credentials.sh <app-id> <env>
   ```

   Subject claim must be exactly
   `repo:<org>/agentic-skill-hub:environment:<env>`.

4. **Deploy infra** (first run requires `az login` from your laptop):

   ```bash
   az deployment group what-if -g rg-skillhub-dev \
     -f infra/main.bicep -p infra/parameters/dev.bicepparam
   az deployment group create -g rg-skillhub-dev \
     -f infra/main.bicep -p infra/parameters/dev.bicepparam
   ```

   Provisions ACR, AKS (Workload Identity + OIDC issuer + AGIC), 5 UAMIs
   with federated credentials bound to
   `system:serviceaccount:skillhub:{frontend,backend,classifier,curator}`,
   Cosmos/Redis/Storage/Key Vault, and the RBAC assignments tying it all
   together.

5. **Seed Key Vault secrets** that can't be auto-generated:

   ```bash
   az keyvault secret set --vault-name kv-skillhub-dev-eastus2 \
     --name apikey-pepper --value "$(openssl rand -hex 32)"
   # Foundry key for the classifier/curator (if review enabled):
   az keyvault secret set --vault-name kv-skillhub-dev-eastus2 \
     --name foundry-api-key --value <key>
   ```

   The SPA is a public client (MSAL PKCE) and the backend validates JWTs
   via JWKS, so there is no `entra-client-secret` to seed.

   Cosmos/Redis/Storage keys are populated by the `rotate-key.yml` workflow.

6. **Install KEDA on the cluster** (one-time, per cluster):

   ```bash
   az aks get-credentials -g rg-skillhub-dev -n aks-skillhub-dev-eastus \
     --overwrite-existing
   helm repo add kedacore https://kedacore.github.io/charts
   helm install keda kedacore/keda --namespace keda --create-namespace
   ```

   KEDA is not bundled in the umbrella chart so cluster operators can
   upgrade it independently.

7. **Set per-environment ingress hosts** in the GitHub Environment
   (`Settings → Environments → dev/staging/prod`):

   - `vars.FRONTEND_HOST` = `skillhub-dev.example.com`
   - `vars.BACKEND_HOST`  = `api.skillhub-dev.example.com`

   These are chart-time inputs, not Bicep outputs.

8. **First deploy via GitHub Actions**:

   ```bash
   gh workflow run deploy-aks.yml -f env=dev
   ```

---

## Image rotation

CI builds and pushes all four images on every deploy. Manual hotfix:

```bash
ACR=skillhubdeveastusacr.azurecr.io
TAG=$(git rev-parse HEAD)
az acr login --name "${ACR%%.*}"
for c in frontend backend classifier curator; do
  docker buildx build --push \
    --tag "$ACR/skillhub-$c:$TAG" \
    --file "Dockerfile.$c" .
done
helm upgrade skillhub charts/agentic-skill-hub \
  -n skillhub \
  --values charts/agentic-skill-hub/values-dev.yaml \
  --set image.tag="$TAG" \
  --reuse-values \
  --atomic --wait --timeout=10m
```

`--reuse-values` keeps the cluster-bound values (UAMI client IDs, ACR
login server, KV name) from the last deploy.

---

## Rolling back

Helm tracks every release as a revision. List + rollback:

```bash
helm history skillhub -n skillhub
helm rollback skillhub <revision> -n skillhub
```

`deploy-aks.yml` invokes `helm rollback` automatically when the post-deploy
`/health` probe never returns 200. `--atomic --wait` rolls back during the
upgrade itself if any Deployment never goes Ready.

**Never delete skill data on rollback.** The chart deploys compute only;
storage (Cosmos / Redis / Blob) is untouched by `helm rollback`. The
never-delete invariant (AGENTS.md §5) is enforced by
`backend/tests/unit/test_never_delete_invariant.py` — `helm rollback`
cannot violate it because no template emits a `delete_*` call.

---

## Troubleshooting

### AGIC 502 / Backend pods Ready but ingress 502

Almost always a NetworkPolicy mismatch. Check:

```bash
kubectl -n skillhub describe networkpolicy skillhub-agentic-skill-hub-backend
kubectl -n skillhub get pod -l app.kubernetes.io/component=backend -o wide
# AGIC pod IP must be in the ingress allow list (or matched by
# `from.podSelector` if AGIC is in the same cluster, addon mode).
```

For addon-mode AGIC: the AGIC pods live in `kube-system`. The chart's
backend NetworkPolicy explicitly allows ingress from `kube-system`.

For BYO App Gateway (prod): traffic arrives from the AGW subnet CIDR.
Make sure the NetworkPolicy `from.ipBlock.cidr` includes the AGW subnet.

### KEDA not scaling the classifier

```bash
kubectl -n skillhub describe scaledobject
kubectl -n keda logs deployment/keda-operator --tail=100
```

Common causes:
- Redis URL on `TriggerAuthentication` wrong (chart reads it from
  `keyVault.secrets.classifier` → `foundry-api-key` is unrelated;
  the redis-key secret is mounted separately).
- Classifier UAMI missing `redis-data-owner` (Entra) on the cache.
- `LLEN queue:classifier` is 0 — KEDA scales to 0 by design. Push a
  message: `redis-cli -h <host> -p 6380 --tls LPUSH queue:classifier '{"smoke":true}'`.

### Curator CronJob skipped

```bash
kubectl -n skillhub get cronjob
kubectl -n skillhub get events --field-selector reason=JobAlreadyActive
```

`concurrencyPolicy: Forbid` skips if the previous run is still active.
This is intentional (AGENTS.md §5). If skips persist:
- Check `key_curator_run_lock` in Redis — a stuck lock with TTL still
  high suggests a previous run died mid-flight. Wait for TTL or
  manually clear with `redis-cli DEL key_curator_run_lock`.
- Check `kubectl -n skillhub get jobs` for a stuck job. Snapshot →
  delete the Job → next CronJob tick fires cleanly.

### Pods stuck Pending

```bash
kubectl -n skillhub describe pod <pod-name>
```

Usually:
- User node pool autoscaler at max → bump
  `userNodePoolMaxCount` in the bicepparam.
- AGIC subnet exhausted → enlarge `agicSubnetCIDR` (addon mode only).
- Workload Identity admission webhook rejecting → check
  `azure.workload.identity/use=true` label is on the pod template (it
  is, by chart construction).

---

## Cosmos data-plane RBAC propagation

Cosmos RBAC assignments take up to ~5 minutes to propagate. On a fresh
deploy, the backend returns 403 from Cosmos for the first few minutes —
expected. `/health` surfaces this clearly.

---

## Phase decisions (M4)

- **AKS over App Service**: per `.agents/plans/m4-aks-deployment.md`. KEDA
  for classifier scale-to-zero; CronJob for the curator schedule;
  one image per component instead of four App Service plans.
- **One frontend image, runtime env**: `/env.js` route emits
  `window.__ENV__`. Same image promoted dev → staging → prod. No
  per-env builds.
- **AGIC addon dev/staging, BYO prod**: addon for simplicity; BYO so
  prod can use a Key Vault cert reference on the AGW listener (TLS at
  AGW, no in-cluster TLS Secret).
- **Azure CNI Overlay + Cilium**: dataplane perf + Cilium NetworkPolicy
  for the per-component egress allowlists.
- **Group claim, not app roles**: per user requirement. Mitigation logged
  in `oidc.py` when neither claim is present.
- **Key-based Cosmos auth in dev/staging, RBAC in prod**: `rbac.bicep`
  assigns Cosmos Data Contributor only when `env == 'prod'` (gated by
  `assignCosmosDataPlane` from `main.bicep`).
