# Infra — Agentic Skill Hub

Bicep modules that describe the M1 Azure footprint for `dev`, `staging`, and `prod`.

## Layout

```
infra/
├── main.bicep                 # top-level composition (one deployment per env)
├── modules/
│   ├── cosmos.bicep           # Cosmos NoSQL account + 4 containers
│   ├── storage.bicep          # Storage account + 4 blob containers
│   ├── redis.bicep            # Azure Cache for Redis
│   ├── keyvault.bicep         # Key Vault (RBAC) + seeded secret names
│   ├── appservice.bicep       # Linux App Service for FastAPI (API)
│   ├── worker.bicep           # Linux App Service for the classifier worker
│   ├── staticwebapp.bicep     # Static Web App for the Next.js frontend
│   ├── appinsights.bicep      # workspace-based App Insights + Log Analytics
│   └── rbac.bicep             # role assignments (KV, Cosmos data plane, Blob)
└── parameters/
    ├── dev.bicepparam
    ├── staging.bicepparam
    └── prod.bicepparam
```

## Naming convention

```
<resource-short>-skillhub-<env>-<region>
```

For example: `kv-skillhub-prod-eastus`, `cosmos-skillhub-dev-eastus`.

Storage account names strip the dashes and truncate to 24 chars.

## Bootstrap a new environment

These steps are **manual** and run **once per environment**. The workflow takes
over from step 5 onwards.

1. **Create the resource group**:

   ```bash
   az group create -n rg-skillhub-dev -l eastus
   ```

2. **Create the Entra App Registrations + admin group** (one command):

   ```bash
   scripts/setup-entra.sh <env> <frontend-hostname>
   # e.g. scripts/setup-entra.sh dev skillhub-dev.example.com
   ```

   This provisions:
   - Backend API registration `skillhub-api-<env>` with scope `access_as_user`
     on identifier URI `api://skillhub-<env>`, group claims enabled.
   - Frontend SPA registration `skillhub-spa-<env>` with SPA redirect URIs
     `https://<frontend-hostname>/auth/callback` and the localhost equivalent,
     pre-authorized for the backend scope (so MSAL login does not prompt for
     consent).
   - Security group `skillhub-admins-<env>` — membership = the `admin` role.

   The script is idempotent and prints a copy-paste block with the IDs to
   drop into `infra/parameters/<env>.bicepparam` and the SWA env vars.
   Optional but recommended for >150-group users: switch the registrations
   to **app roles** afterwards to dodge the `groups` claim overflow.

3. **Set up GitHub federated credentials** so the deploy workflow can `az login`
   without a stored client secret:

   ```bash
   scripts/setup_federated_credentials.sh <app-id> <env>
   ```

   Subject claim must be exactly `repo:<org>/agentic-skill-hub:environment:<env>`.

4. **Deploy infra** (first run requires `az login` from your laptop):

   ```bash
   az deployment group what-if -g rg-skillhub-dev \
     -f infra/main.bicep -p infra/parameters/dev.bicepparam
   az deployment group create -g rg-skillhub-dev \
     -f infra/main.bicep -p infra/parameters/dev.bicepparam
   ```

5. **Seed Key Vault secrets** that can't be auto-generated:

   ```bash
   # Entra client secret (from step 2)
   az keyvault secret set --vault-name kv-skillhub-dev-eastus \
     --name entra-client-secret --value <secret>
   # Generate a strong API key pepper
   az keyvault secret set --vault-name kv-skillhub-dev-eastus \
     --name apikey-pepper --value "$(openssl rand -hex 32)"
   ```

   Cosmos/Redis/Storage keys are populated by the `rotate-key.yml` workflow.

6. **First deploy via GitHub Actions**:

   ```bash
   gh workflow run deploy.yml -f env=dev
   ```

## Cosmos data-plane RBAC propagation

Cosmos RBAC assignments take up to ~5 minutes to propagate. On a fresh deploy,
the API will return 403 from Cosmos for the first few minutes — this is
expected. `/healthz` surfaces this clearly.

## Phase decisions

- **Worker on App Service, not Functions** (for M1). Same Bicep pattern as the
  API, trivially swappable in M4 if scale demands elastic compute.
- **Key-based Cosmos auth in dev/staging, RBAC in prod**. `infra/modules/rbac.bicep`
  only assigns the Cosmos Data Contributor role when `env == 'prod'` (gated by
  the `assignCosmosDataPlane` parameter, set in `main.bicep`). Dev/staging use
  the master key from KV; prod uses managed identity + RBAC.
- **Group claim, not app roles** (per user requirement). Mitigation logged in
  `oidc.py` when neither claim is present.
