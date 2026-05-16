# Feature: M1 — Azure Deployment + Auth

The following plan should be complete, but it is important that you validate documentation, codebase patterns, and task sanity before you start implementing. M0 is already merged (FastAPI backend, Next.js frontend, classifier worker, docker-compose stack, all tests green). M1 layers Azure infra, real OIDC auth, machine API keys, CI/CD, and observability on top of the M0 spine **without rewriting M0 business logic**.

Pay special attention to:
- The `IdentityProvider` abstraction MUST be additive — `backend/core/auth.py` keeps `User`, `Role`, `get_current_user`, and `require_role` as the public surface. Only the *provider implementation* changes between stub / OIDC / (future) SAML.
- Cosmos-first writes and the four Redis rules from `AGENTS.md` §3–§4 remain in force in cloud — Key Vault and App Insights do not get a pass.
- Two-role split is **user** + **admin** (collapsed from M0's `contributor`/`manager`/`admin`). `manager` semantics move under `admin`. This is a breaking change to the role names and requires updating every `require_role("manager")` call site.

## Feature Description

Take the M0 POC (working end-to-end on local emulators) and ship it into Azure across three environments (dev, staging, prod), replacing the `X-User-Email` header stub with Entra ID OIDC for humans and HMAC-signed API keys for agent runtimes. Infrastructure is described as Bicep modules deployed by GitHub Actions, secrets live exclusively in Key Vault (App Service pulls via Key Vault references — no inline env-var secrets), and Application Insights captures structured logs + distributed traces for the API, the worker, and the Next.js frontend.

A key design constraint: authentication is wrapped behind an `IdentityProvider` Protocol so that the M0 stub provider, the M1 OIDC provider, and a future SAML provider are interchangeable behind one env flag (`AUTH_MODE=stub|oidc|saml`). Downstream code — every route, service, audit row — keeps depending only on `User` and `require_role` and never touches OIDC primitives.

A second, separate code path handles **machine identity**: agent runtimes call `Authorization: Bearer <api-key>` and resolve to a `ServiceAccount` (not a `User`). This is intentionally not merged with the human auth flow — different lifecycle, different scopes, different audit surface — but both flow through a single `Principal` union so route handlers can `Depends(get_principal)` when they accept either.

## User Story

As an **org admin** I want the Skill Hub running in Azure behind Entra ID with role-based access, so real contributors can sign in with their corporate identity and see only what they're entitled to, while agent runtimes authenticate with rotatable API keys, and every secret lives in Key Vault — not on a laptop.

As a **platform engineer** I want a single `gh workflow run deploy.yml -f env=dev` to provision the entire stack from Bicep and ship the latest commit, with App Insights showing me end-to-end traces from the Next.js page → FastAPI route → Cosmos query within minutes of the deploy completing.

As a **future me adding SAML** I want to drop a new `SamlIdentityProvider` class into `backend/core/auth/providers/` and flip `AUTH_MODE=saml`, without grepping the codebase for every place that reads a user's email.

## Problem Statement

M0 works on a laptop with emulators. There is no Azure footprint, no real auth (any caller can pose as anyone via `X-User-Email`), no machine identity story (agent runtimes have no credential model), no CI/CD (deploys would be manual), no secrets management (the Cosmos key is a defaulted constant), and no production observability (logs are stdout JSON, no traces). The three-role split from M0 (`contributor` / `manager` / `admin`) is also more granular than the product actually needs — managers and admins do the same things in practice — and we want to collapse to **user** / **admin** before real customers start mapping Entra groups against the wrong role names.

If we ship M1 without an auth abstraction, the next identity migration (SAML for a customer that doesn't have Entra) becomes a multi-week rewrite. If we ship it without Key Vault references on App Service, secrets end up in deploy logs. If we ship it with App Insights wired only to the API, the worker becomes the blind spot where every interesting failure happens.

## Solution Statement

1. **Infra as Bicep.** `infra/` contains one `main.bicep` per environment (`dev`, `staging`, `prod`) plus modules for each resource type. Resources: Cosmos DB (Serverless in dev, Provisioned in staging/prod), Storage Account (Blob with private endpoint in prod), Azure Cache for Redis (Basic in dev, Standard in staging/prod, AOF persistence enabled on staging/prod), Linux App Service (FastAPI), Linux App Service or Static Web App (Next.js), Function App (classifier worker — Linux Premium for VNet integration in prod), Key Vault (RBAC mode, soft-delete + purge protection on prod), Application Insights (workspace-based), Log Analytics Workspace, an Entra App Registration per environment (managed out-of-band but referenced).

2. **Pluggable `IdentityProvider`.** `backend/core/auth.py` is refactored: the public dep `get_current_user` stays identical in signature; internally it delegates to a singleton `IdentityProvider` chosen at app startup based on `AUTH_MODE`. Providers live in `backend/core/auth/providers/{stub,oidc,saml}.py`. The OIDC provider validates Entra-issued JWTs against the tenant JWKS, extracts `email` and the `groups` claim, and maps groups → `Role` server-side using `ENTRA_GROUP_ID_ADMIN` (everything not in that group is `user`).

3. **Two-role collapse.** `Role = Literal["user", "admin"]`. `require_role("admin")` gates the review queue, approve/reject, classification overrides, future curator controls. `require_role("user")` (or just `get_current_user`) gates upload, my-submissions, browse catalog. Every M0 call site that used `manager` is rewritten to `admin`. A migration note in the audit log explains the rename for historical rows.

4. **Machine identity via API keys.** New `backend/core/auth/api_keys.py` issues opaque keys (`sh_live_<32 random bytes base64url>`), stores a SHA-256 hash + scopes + owner + created/last_used in a new Cosmos container `api_keys` (PK `/key_id`), and resolves an incoming `Authorization: Bearer <key>` into a `ServiceAccount`. Rotation = issue new + revoke old; revocation is a soft flag (`revoked_at`) so the audit trail survives. A new admin route `POST /v1/admin/api-keys` issues, `GET` lists, `DELETE /{key_id}` revokes. Catalog read endpoints accept either a `User` or a `ServiceAccount` via a unified `Principal = User | ServiceAccount` and `get_principal` dep.

5. **GitHub Actions CI/CD.** `.github/workflows/ci.yml` runs on every PR: ruff, pytest unit + integration (using the docker-compose stack inside the runner), pyright, frontend lint + typecheck + build. `.github/workflows/deploy.yml` is `workflow_dispatch` with an `env` input (`dev|staging|prod`), uses environment protection rules (manual approval for staging + prod), authenticates to Azure via OIDC federated credentials (no secrets), runs `az deployment group create` against the env-specific Bicep, then deploys API + worker + frontend artifacts. Prod requires two reviewers.

6. **Key Vault wiring.** App Service references secrets via `@Microsoft.KeyVault(SecretUri=...)` syntax in `appSettings`. Settings class adds nothing — Pydantic still reads from env, but the env values are populated by the platform from Key Vault at app start. The Bicep grants the App Service's system-assigned managed identity `Key Vault Secrets User` on the vault. No app code reads from Key Vault directly.

7. **App Insights wiring.** Backend adds the `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx`, `azure-monitor-opentelemetry` packages and a `backend/core/telemetry.py` that configures OTel when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set (no-op locally). The classifier worker calls the same init in its entrypoint. Frontend adds `@microsoft/applicationinsights-web` with the connection string injected at build time via `NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING`. Structured logs flow into App Insights `traces`; OTel spans flow into `dependencies` + `requests`.

## Feature Metadata

**Feature Type**: New Capability (auth, infra, CI/CD layered on existing app) + Refactor (role rename + auth provider abstraction)
**Estimated Complexity**: High (Bicep, Entra app registrations, OIDC JWT validation, Key Vault references, GitHub OIDC federated creds, OTel wiring across three runtimes)
**Primary Systems Affected**: `backend/core/auth*`, `backend/api/*` (role rename + principal dep), `backend/app.py` (telemetry boot), `backend/workers/classifier.py` (telemetry boot), `frontend/lib/api/client.ts` + new auth hook, new `infra/`, new `.github/workflows/`, new Cosmos container `api_keys`.
**Dependencies**:
- Python adds: `pyjwt[crypto]>=2.9`, `azure-identity>=1.17`, `azure-keyvault-secrets>=4.8` (only for an admin rotation CLI; runtime uses Key Vault refs), `azure-monitor-opentelemetry>=1.6`, `opentelemetry-instrumentation-fastapi>=0.48b0`, `opentelemetry-instrumentation-httpx>=0.48b0`, `opentelemetry-instrumentation-redis>=0.48b0`.
- Frontend adds: `@microsoft/applicationinsights-web@^3`, `@azure/msal-browser@^3`, `@azure/msal-react@^2` (or NextAuth.js with the Entra provider — pick one in Phase 1; default below is MSAL because we already speak OIDC natively).
- Infra: Bicep CLI (bundled with `az`), `az` 2.60+, GitHub `azure/login@v2` with `auth-type: IDENTITY` (OIDC).
- Out-of-band: an Entra tenant with App Registration permissions, three resource groups (`rg-skillhub-dev`, `-staging`, `-prod`), federated credential subject claims for the GitHub repo.

---

## CONTEXT REFERENCES

### Relevant Codebase Files IMPORTANT: YOU MUST READ THESE FILES BEFORE IMPLEMENTING!

- `AGENTS.md` (entire file) — Especially §3 (storage split — does not change in cloud), §4 (four Redis rules — still binding behind real Redis), §8 (DI patterns — auth providers follow the same wiring), §10 (CI must enforce all gates).
- `docs/PRD.md` §12 lines 517–527 — M1 deliverables (Bicep, OIDC, API keys, CI/CD, App Insights).
- `.agents/plans/m0-poc-end-to-end-skill-submission.md` — M0 plan; section "NOTES" calls out exactly which M0 shortcuts M1 must close (staging blob container for bundle bytes, real Cosmos TLS, audit container RBAC, frontend localStorage → real auth).
- `backend/core/auth.py` (lines 1–66) — Current stub. `User`, `Role`, `get_current_user`, `require_role`. This file is the seam; refactor into a package while preserving these names.
- `backend/core/config.py` (lines 22–84) — `Settings` already has `auth_mode: Literal["stub", "oidc"]`. Extend with OIDC + Entra group + API-key + telemetry settings. **Do not break defaults** — local dev must still boot with no env vars.
- `backend/core/errors.py` (lines 50–62) — `Forbidden`, `Unauthorized`. Reuse; add `InvalidToken`, `RevokedApiKey`, `MissingScope` with the same pattern.
- `backend/core/deps.py` (lines 24–49) — DI factories. Add `get_principal`, `get_identity_provider`.
- `backend/app.py` (lines 36–69) — Lifespan. Add telemetry init at top, identity-provider init after settings.
- `backend/api/admin.py` (lines 31, all `require_role` calls) — Currently uses `require_role("manager")`. Rename to `"admin"`.
- `backend/api/uploads.py` — Uses `get_current_user` directly; no change to signatures, but the underlying provider changes.
- `backend/api/skills.py` — Public catalog. After M1, `get_skill`/`list_skills`/`download` accept `Depends(get_principal)` so agent runtimes can call them with API keys; humans still work via OIDC.
- `backend/workers/classifier.py` — Add `init_telemetry()` at top of `main()` so worker traces flow to App Insights.
- `backend/services/audit.py` — `actor` field stays as a string; for `ServiceAccount` callers write `actor=f"svc:{service_account_id}"` so audit rows differentiate humans from machines.
- `backend/tests/conftest.py` — Test client currently sets `X-User-Email`. Add a fixture `as_admin()` that overrides the `IdentityProvider` dep to return an admin `User` directly; do not hardcode auth-mode-specific values in tests.
- `frontend/lib/api/client.ts` (lines 10–33) — `getStubUser()` + `headers.set("X-User-Email", ...)`. Replace with a token-aware client that reads an MSAL access token and sends `Authorization: Bearer <token>`. Keep stub mode behind a build flag for offline dev.
- `frontend/components/UserPicker.tsx` — Stub user dropdown; replaced in M1 by an MSAL sign-in/sign-out button. Keep the file under a `stub-only` feature flag for local emulator runs.
- `frontend/app/layout.tsx` (lines 12–43) — Wrap `<body>` in an `<MsalProvider>` (client component).
- `docker-compose.yml` — Unchanged. Local dev keeps emulators + stub auth. M1 must not regress the zero-Azure-spend local loop (`AGENTS.md` §6).
- `Makefile` (lines 25–64) — Add `make bicep-what-if ENV=dev`, `make deploy ENV=dev`, `make rotate-key NAME=...`.
- `pyproject.toml` (lines 7–19, 22–27) — Add deps listed above. Pin OTel packages to a matched version.
- `.env.local.example` — Add documented (commented-out) `AUTH_MODE=oidc`, `ENTRA_TENANT_ID=`, `ENTRA_CLIENT_ID=`, `ENTRA_GROUP_ID_ADMIN=`, `APPLICATIONINSIGHTS_CONNECTION_STRING=`, `APIKEY_PEPPER=` blocks for cloud parity.

### New Files to Create

**Infra (`infra/`)**
- `infra/main.bicep` — Entry point; takes `env` (string), `location` (string), composes modules.
- `infra/modules/cosmos.bicep` — Cosmos account + DB + containers (`skills`, `audit`, `usage_events`, `api_keys`). Serverless for dev, Standard for staging/prod. TTL on `usage_events`. Outputs: account name, primary key secret URI.
- `infra/modules/storage.bicep` — Storage Account + blob containers (`published`, `archive`, `snapshots`, `staging`). Private endpoint flag (true for prod). Outputs: account name, connection string secret URI.
- `infra/modules/redis.bicep` — Azure Cache for Redis. Basic C0 (dev), Standard C1 (staging), Premium P1 with AOF (prod). Outputs: hostname, primary key secret URI.
- `infra/modules/keyvault.bicep` — Key Vault, RBAC mode, soft-delete on, purge protection on for prod. Diagnostic settings → Log Analytics.
- `infra/modules/appservice.bicep` — App Service Plan (Linux) + Web App for FastAPI. System-assigned MI. `appSettings` populated with Key Vault references. Outputs: principal ID (for RBAC grants), default hostname.
- `infra/modules/functions.bicep` — Function App (Linux, Python 3.12) for classifier worker. System-assigned MI. Always-on. `appSettings` from Key Vault refs. (Alternative: deploy the worker as a second App Service container — see Phase 1 decision note in `infra/README.md`.)
- `infra/modules/staticwebapp.bicep` — Azure Static Web App for Next.js frontend (or Linux App Service if SSR features are needed — pick during Phase 1).
- `infra/modules/appinsights.bicep` — Workspace-based App Insights + Log Analytics workspace. Outputs: connection string secret URI.
- `infra/modules/rbac.bicep` — Role assignments: App Service MI → Key Vault Secrets User, App Service MI → Cosmos DB Data Contributor (via `Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments`), App Service MI → Storage Blob Data Contributor. Same for Function App MI.
- `infra/modules/secrets.bicep` — Seeds Key Vault with secret *names* (values populated post-deploy by the deploy workflow or manually for Entra client secret). One secret per: `cosmos-key`, `blob-connection-string`, `redis-primary-key`, `appinsights-connection-string`, `entra-client-secret`, `apikey-pepper`.
- `infra/parameters/dev.bicepparam`, `staging.bicepparam`, `prod.bicepparam` — Per-env values.
- `infra/README.md` — Resource layout, naming convention (`<resource>-skillhub-<env>-<region>`), how to bootstrap a new env, federated credential setup steps.

**Backend (`backend/core/auth/`)** — Refactor the single file into a package:
- `backend/core/auth/__init__.py` — Re-exports `User`, `Role`, `Principal`, `ServiceAccount`, `get_current_user`, `get_principal`, `require_role`. Keep the import path `from backend.core.auth import User, require_role` working — no caller changes.
- `backend/core/auth/models.py` — `Role = Literal["user", "admin"]`, `User`, `ServiceAccount(BaseModel)` with `service_account_id: str`, `name: str`, `scopes: list[Scope]`, `Principal = Union[User, ServiceAccount]`, helpers `has_role`, `has_scope`.
- `backend/core/auth/providers/base.py` — `IdentityProvider(Protocol)` with `async def resolve(request: Request) -> User`. `select_provider(settings) -> IdentityProvider`.
- `backend/core/auth/providers/stub.py` — Current header logic, lifted as-is.
- `backend/core/auth/providers/oidc.py` — Validates `Authorization: Bearer <jwt>` against Entra JWKS (cached via `httpx_cache` or a manual TTL cache; **TTL counts as a Redis rule #3 concern** if we put it in Redis, otherwise process-local), extracts `preferred_username`/`email` and `groups`, maps to `Role`. Raises `InvalidToken` on signature/iss/aud/exp failure.
- `backend/core/auth/providers/saml.py` — Stub class with `NotImplementedError` and a TODO pointing at the M-future SAML milestone. Existence forces the abstraction to stay honest.
- `backend/core/auth/api_keys.py` — Issue, hash (SHA-256 over `pepper + raw_key`), lookup (`async def resolve_api_key(token, *, api_keys_container) -> ServiceAccount`), revoke. Cosmos-first writes; cache lookups in Redis under `cache:apikey:{hash_prefix}` with 60s TTL + Cosmos fallback (rules #1, #2, #3).
- `backend/core/auth/deps.py` — `get_identity_provider(request)` (pulls from app.state), `get_current_user`, `get_principal` (tries Bearer → API key first, falls back to user provider), `require_role(role)`, `require_scope(scope)`.

**Backend (other)**
- `backend/core/telemetry.py` — `configure_telemetry(settings)` — no-op when connection string is empty; otherwise calls `configure_azure_monitor()` + instruments FastAPI, httpx, redis-py. Idempotent.
- `backend/models/api_key.py` — `ApiKeyDoc` (Cosmos shape), `ApiKeyIssueRequest`, `ApiKeyIssueResponse` (returns raw key exactly once), `ApiKeyListItem`.
- `backend/api/api_keys.py` — `POST /v1/admin/api-keys`, `GET /v1/admin/api-keys`, `DELETE /v1/admin/api-keys/{key_id}`. Admin-only.
- `backend/services/api_keys.py` — Business logic, audit on issue/revoke.
- `backend/tests/unit/test_auth_oidc.py` — Validates JWT happy path + tampered token + wrong audience + expired token (use `pyjwt` to mint test tokens against a fixture RSA keypair).
- `backend/tests/unit/test_api_keys.py` — Issue → hash → resolve → revoke → resolve raises.
- `backend/tests/unit/test_role_mapping.py` — Group claim → role mapping table.
- `backend/tests/integration/test_principal_dep.py` — Route accepts both a User (via fake OIDC) and a ServiceAccount (via API key) through the same `Depends(get_principal)`.
- `backend/tests/integration/test_apikey_cache_fallback.py` — API key resolves from Cosmos when Redis is down (rule #2).

**Frontend**
- `frontend/lib/auth/msalConfig.ts` — `PublicClientApplication` config with `clientId`, `authority`, `redirectUri` from `NEXT_PUBLIC_*` env vars.
- `frontend/lib/auth/MsalProvider.tsx` — Client component wrapping `@azure/msal-react`'s `MsalProvider`; renders `<SignInGate>` if not signed in.
- `frontend/lib/auth/useAccessToken.ts` — Hook returning a function `getAccessToken(): Promise<string>` using `acquireTokenSilent` with the API scope.
- `frontend/lib/api/client.ts` — **UPDATE**: replace `getStubUser()` with `getAccessToken()`; send `Authorization: Bearer <jwt>`. Behind `NEXT_PUBLIC_AUTH_MODE=stub`, keep the M0 header path.
- `frontend/lib/telemetry/appInsights.ts` — Initializes `ApplicationInsights` from `NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING`; exposes `trackPageView`, `trackException`.
- `frontend/app/layout.tsx` — **UPDATE**: wrap children in `<MsalProvider>` and `<AppInsightsBoundary>`.
- `frontend/components/UserPicker.tsx` — **UPDATE**: behind stub mode only. In OIDC mode, render `<SignInOutButton>` instead.

**Workflows**
- `.github/workflows/ci.yml` — Push/PR. Jobs: `backend-lint-and-test`, `frontend-lint-and-test`, `bicep-lint-and-whatif` (against dev RG).
- `.github/workflows/deploy.yml` — `workflow_dispatch` with `env: choice(dev, staging, prod)`. Jobs: `bicep-deploy`, `backend-deploy` (zip deploy to App Service), `worker-deploy` (Function App publish or App Service container), `frontend-deploy` (SWA CLI or `az staticwebapp`). Uses `environment: ${{ inputs.env }}` for protection rules + per-env secrets.
- `.github/workflows/rotate-key.yml` — `workflow_dispatch` to rotate a named secret in Key Vault (regenerates Cosmos/Redis/Storage keys, writes new value to Key Vault, restarts App Service so it picks up the reference).
- `.github/CODEOWNERS` — `infra/**` requires platform team review.

**Scripts**
- `scripts/setup_federated_credentials.sh` — Documented one-time: creates the federated identity credential on the App Registration so GitHub can `az login` without secrets.
- `scripts/issue_api_key.py` — Local CLI wrapping `POST /v1/admin/api-keys` for bootstrapping an agent runtime in dev.
- `scripts/mint_test_jwt.py` — Test helper for backend OIDC tests; not deployed.

### Relevant Documentation YOU SHOULD READ THESE BEFORE IMPLEMENTING!

- [Microsoft Entra ID — Validate tokens (v2.0)](https://learn.microsoft.com/azure/active-directory/develop/access-tokens#validating-tokens) — Required claim checks: `iss`, `aud`, `exp`, `nbf`, signature against tenant JWKS at `https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys`. **The `groups` claim is omitted when a user is in too many groups — fall back to the Graph `/me/getMemberObjects` endpoint or, preferred, configure the app registration to emit only the `admin` group as a role claim.**
- [Entra ID — App roles vs group claims](https://learn.microsoft.com/azure/active-directory/develop/howto-add-app-roles-in-apps) — Prefer **app roles** if the org agrees: claim is bounded, no overflow, simpler mapping. Plan defaults to group claim per the user's requirement; document the app-roles alternative.
- [MSAL.js for React](https://learn.microsoft.com/azure/active-directory/develop/tutorial-v2-react) — Wiring `MsalProvider`, `useMsal`, `acquireTokenSilent`.
- [Azure Functions Python developer guide (v2 programming model)](https://learn.microsoft.com/azure/azure-functions/functions-reference-python?pivots=python-mode-decorators) — If we deploy the worker as a Function. Alternative is to deploy it as a second App Service running `python -m backend.workers.classifier` with `WEBSITES_CONTAINER_START_TIME_LIMIT=1800` — simpler operationally, same Bicep pattern as the API. **Recommendation: App Service for M1, Functions for M4+ when load justifies elastic scale.**
- [App Service — Use Key Vault references](https://learn.microsoft.com/azure/app-service/app-service-key-vault-references) — Syntax `@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/<name>/)`. App Service auto-refreshes references on restart.
- [Azure Bicep modules — Best practices](https://learn.microsoft.com/azure/azure-resource-manager/bicep/best-practices) — Module composition, `output` patterns, parameter files.
- [Azure Monitor OpenTelemetry for Python](https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-enable?tabs=python) — `configure_azure_monitor()` + FastAPI/HTTPX/Redis instrumentations.
- [GitHub Actions — OIDC federated credentials with Azure](https://learn.microsoft.com/azure/developer/github/connect-from-azure?tabs=azure-cli) — Lets `azure/login@v2` work with `auth-type: IDENTITY` and zero stored client secrets.
- [GitHub Environments + required reviewers](https://docs.github.com/actions/deployment/targeting-different-environments/using-environments-for-deployment) — How `staging`/`prod` get manual gates.
- [Cosmos DB — Data plane RBAC (NoSQL)](https://learn.microsoft.com/azure/cosmos-db/how-to-setup-rbac) — Assign `Cosmos DB Built-in Data Contributor` to the App Service MI so we can use `DefaultAzureCredential` + drop the Cosmos *key* secret entirely. **Recommendation: do this in prod; keep key-based for dev/staging to keep iteration fast.**
- [PyJWT — JWKS verification recipes](https://pyjwt.readthedocs.io/en/stable/usage.html#retrieve-rsa-signing-keys-from-a-jwks-endpoint) — Reference for OIDC provider implementation.

### Patterns to Follow

**Provider abstraction (mandatory)** — The seam:
```python
# backend/core/auth/providers/base.py
class IdentityProvider(Protocol):
    async def resolve(self, request: Request) -> User: ...

def select_provider(settings: Settings) -> IdentityProvider:
    match settings.auth_mode:
        case "stub": return StubIdentityProvider(settings)
        case "oidc": return OidcIdentityProvider(settings)
        case "saml": return SamlIdentityProvider(settings)  # raises NotImplementedError
```
The `get_current_user` dep stays a one-liner: `return await request.app.state.identity_provider.resolve(request)`. **No route handler imports any provider class directly.**

**Principal union (mandatory)**:
```python
# backend/core/auth/deps.py
async def get_principal(request: Request, ...) -> Principal:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(None, 1)[1]
        if token.startswith("sh_live_"):           # machine
            return await resolve_api_key(token, api_keys_container=...)
        # else: fall through to OIDC user resolution
    return await request.app.state.identity_provider.resolve(request)
```
`Authorization: Bearer sh_live_...` → ServiceAccount. `Authorization: Bearer <jwt>` → User. Stub mode still uses `X-User-Email` and only resolves to User.

**Cosmos-first writes (unchanged from M0, repeated for emphasis)** — API-key issue:
```python
# backend/services/api_keys.py
async def issue(name, scopes, actor, *, api_keys, audit, redis):
    raw = f"sh_live_{secrets.token_urlsafe(32)}"
    doc = ApiKeyDoc(
        key_id=uuid.uuid4().hex, name=name, scopes=scopes,
        hash_sha256=_hash(raw, settings.apikey_pepper),
        created_by=actor, created_at=utcnow(), revoked_at=None,
    )
    await api_keys.create_item(body=doc.model_dump(mode="json"))   # 1. Cosmos FIRST
    await audit.record(skill_id=f"apikey:{doc.key_id}", action="apikey_issue", actor=actor, after={...})  # 2. audit
    # No Redis pre-warm — the cache populates on first resolve. Rule #3: TTL on lookup, not on key list.
    return ApiKeyIssueResponse(key_id=doc.key_id, raw_key=raw, name=name, scopes=scopes)
```
Revoke flips `revoked_at` in Cosmos, audits, **then** `redis.delete("cache:apikey:" + hash_prefix)` to invalidate.

**Audit `actor` convention**:
- Human caller → `actor = user.email` (M0 behavior)
- Machine caller → `actor = f"svc:{service_account.service_account_id}"`
- Tests must assert this format so we never silently mix the two surfaces.

**Bicep naming + outputs**:
```
{resource-short}-skillhub-{env}-{region}
e.g. kv-skillhub-prod-eus, cosmos-skillhub-dev-eus
```
Every module returns its principal-relevant outputs (resource ID, hostname, secret URIs) so the parent `main.bicep` can wire RBAC without re-deriving names.

**App Service appSettings — Key Vault refs (never inline secrets)**:
```bicep
appSettings: [
  { name: 'COSMOS_ENDPOINT', value: cosmos.outputs.endpoint }
  { name: 'COSMOS_KEY', value: '@Microsoft.KeyVault(SecretUri=${kv.outputs.cosmosKeyUri})' }
  { name: 'REDIS_URL', value: '@Microsoft.KeyVault(SecretUri=${kv.outputs.redisUrlUri})' }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: '@Microsoft.KeyVault(SecretUri=${kv.outputs.appiUri})' }
  // ... no raw secret values, ever
]
```

**TypeScript auth client**:
```ts
// frontend/lib/api/client.ts (M1 shape)
async function call<T>(path, init = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (process.env.NEXT_PUBLIC_AUTH_MODE === "stub") {
    headers.set("X-User-Email", getStubUser());
  } else {
    const token = await getAccessToken();
    headers.set("Authorization", `Bearer ${token}`);
  }
  // ...rest identical to M0
}
```

---

## IMPLEMENTATION PLAN

### Phase 1: Foundation — auth abstraction + role collapse (backend-only, no Azure spend)
Ship the refactor so M0 continues to pass on the local stack with `AUTH_MODE=stub`, but every seam needed for OIDC + API keys is in place. This phase is *non-breaking on the wire* but renames roles internally.

**Tasks:**
- Add new auth deps to `pyproject.toml` (`pyjwt[crypto]`, `azure-identity`, OTel packages).
- Convert `backend/core/auth.py` (single file) into `backend/core/auth/` package; re-export everything so `from backend.core.auth import ...` keeps working.
- Add `Role = Literal["user", "admin"]` and a one-shot migration: rename `require_role("manager")` → `require_role("admin")` in `backend/api/admin.py`; drop the `contributor`/`manager` literals from `User.roles` mapping. Anyone in `settings.admin_emails` → `["user", "admin"]`; everyone else → `["user"]`. (The stub provider keeps `manager_emails` as an alias for backward compat for one release, with a deprecation log line.)
- Add `IdentityProvider` Protocol + `StubIdentityProvider` + `select_provider`.
- Add `Principal` union + `ServiceAccount` + `get_principal` dep (Bearer → API key path will be wired in Phase 3, but the dep shape lands now so Phase 4 routes can adopt it).
- Add `InvalidToken`, `RevokedApiKey`, `MissingScope` to `errors.py`.
- Update `backend/tests/conftest.py` to override `app.state.identity_provider` with an in-memory provider that returns a configured `User`; this replaces the `X-User-Email` test header pattern.
- Verify: `make test` green, `make demo` green, `AUTH_MODE=stub` still works, frontend unchanged.

### Phase 2: Telemetry wiring (backend + worker + frontend), still no Azure spend
**Tasks:**
- Add `backend/core/telemetry.py` with `configure_telemetry(settings)` — no-op when conn string is empty.
- Call it at the top of `lifespan()` in `backend/app.py` and at the top of `main()` in `backend/workers/classifier.py`.
- Add `frontend/lib/telemetry/appInsights.ts` and gate on `NEXT_PUBLIC_APPINSIGHTS_CONNECTION_STRING`.
- Verify: local boot has zero OTel chatter; setting a dummy connection string + the real package emits spans to stdout exporter (sanity).

### Phase 3: OIDC provider + API keys
**Tasks:**
- Implement `OidcIdentityProvider` with JWKS fetch + in-process TTL cache (1 hour) + claim mapping.
- Add `api_keys` Cosmos container creation to `backend/core/cosmos.py` (`ensure_containers`).
- Implement `backend/core/auth/api_keys.py` (issue/hash/resolve/revoke) with Redis cache + Cosmos fallback.
- Implement `backend/api/api_keys.py` (admin-only CRUD) and register in `backend/app.py`.
- Wire `get_principal` to recognize `Authorization: Bearer sh_live_...`.
- Update `backend/api/skills.py` `list_skills` / `get_skill` / `download_skill` to use `Depends(get_principal)` (humans and agents both allowed). `uploads.py` and `admin.py` stay on `get_current_user` / `require_role("admin")` (humans only).
- Tests: OIDC happy/tampered/expired/wrong-aud, API key issue→resolve→revoke, Redis-down fallback for key resolution, audit row format for `svc:` actor.

### Phase 4: Frontend OIDC
**Tasks:**
- Add MSAL deps + `MsalProvider` wrapper + `useAccessToken` hook + `<SignInOutButton>`.
- Replace `getStubUser` path in `frontend/lib/api/client.ts` with token-aware path, gated by `NEXT_PUBLIC_AUTH_MODE`.
- Add a build-time env check that fails the build if `NEXT_PUBLIC_AUTH_MODE !== "stub"` and any required Entra var is missing.
- Manual verification against an actual Entra test tenant before cloud deploy.

### Phase 5: Bicep + Key Vault wiring
**Tasks:**
- Author `infra/main.bicep` + every module under `infra/modules/`.
- Author `dev.bicepparam`, `staging.bicepparam`, `prod.bicepparam`.
- `az deployment group what-if` against `rg-skillhub-dev` until clean.
- `az deployment group create` to dev. Manually seed Key Vault secrets that can't be auto-generated (the Entra client secret).
- Verify health endpoint on the deployed App Service.

### Phase 6: GitHub Actions CI/CD
**Tasks:**
- Author `.github/workflows/ci.yml` — runs on every PR, requires green.
- Author `.github/workflows/deploy.yml` — manual dispatch; dev = no approval, staging = 1 reviewer, prod = 2 reviewers.
- Configure federated credentials per environment (subject `repo:org/agentic-skill-hub:environment:dev` etc.).
- Author `.github/workflows/rotate-key.yml`.
- Add `.github/CODEOWNERS`.

### Phase 7: End-to-end validation in cloud
**Tasks:**
- Deploy to dev, run the M0 happy-path e2e test pointed at the deployed API (`API_BASE=https://api-skillhub-dev-eus.azurewebsites.net`).
- Real human signs in via Entra, uploads a skill, observes status walk through OIDC.
- Issue an API key, use it from a script to `GET /v1/skills` and download a bundle.
- Verify App Insights end-to-end trace: frontend page view → API request → Cosmos dependency span.
- Verify rotation workflow: rotate Cosmos key, App Service restarts cleanly, no downtime visible to a polling client.
- Cut over staging and prod with manual approvals.

---

## STEP-BY-STEP TASKS

IMPORTANT: Execute every task in order, top to bottom. Each task is atomic and independently testable.

### UPDATE `pyproject.toml`
- **IMPLEMENT**: Add to `dependencies`: `pyjwt[crypto]>=2.9`, `azure-identity>=1.17`, `azure-monitor-opentelemetry>=1.6`, `opentelemetry-instrumentation-fastapi>=0.48b0`, `opentelemetry-instrumentation-httpx>=0.48b0`, `opentelemetry-instrumentation-redis>=0.48b0`. Pin OTel `instrumentation-*` packages to the same minor as `azure-monitor-opentelemetry` declares to avoid Hyrum-style breakage.
- **GOTCHA**: `azure-monitor-opentelemetry` brings its own OTel SDK version; do NOT also pin `opentelemetry-sdk` directly or you'll resolve a conflict.
- **VALIDATE**: `uv sync` (or `pip install -e .[dev]`) resolves clean. `python -c "import azure.monitor.opentelemetry, jwt; print('ok')"`.

### REFACTOR `backend/core/auth.py` → `backend/core/auth/` package
- **IMPLEMENT**: Create `backend/core/auth/__init__.py` re-exporting `User`, `Role`, `Principal`, `ServiceAccount`, `get_current_user`, `get_principal`, `require_role`, `require_scope`. Move `User` + role logic to `models.py`. Move the stub header logic verbatim into `providers/stub.py`. Add `providers/base.py` (Protocol + `select_provider`). Add `providers/saml.py` (raises `NotImplementedError("SAML provider lands in a future milestone")` on `resolve`).
- **PATTERN**: Mirror `backend/services/__init__.py` style — package is the public surface.
- **IMPORTS**: Existing `from backend.core.auth import User, require_role, get_current_user` must continue to work without changes. Run `rg "from backend.core.auth"` and confirm zero diff in call sites.
- **GOTCHA**: Python import order — the package `__init__` must avoid circular imports between `models.py` and `deps.py` (put deps in a separate submodule and import lazily inside `__init__` if needed).
- **VALIDATE**: `uv run pytest backend/tests/unit/test_auth.py -v` passes unchanged. `rg "from backend.core.auth" backend/ frontend/` shows no broken imports.

### UPDATE `backend/core/auth/models.py` — collapse to two roles
- **IMPLEMENT**: `Role = Literal["user", "admin"]`. `User.roles: list[Role]`. `_roles_for(email, settings)` returns `["user", "admin"]` if email in `settings.admin_email_set()` else `["user"]`. `settings.manager_email_set()` is read for one release with a `WARN` log "manager_emails is deprecated; use admin_emails" — then dropped in a follow-up.
- **GOTCHA**: M0 tests assert `["contributor", "manager", "admin"]`-style lists. Update those assertions in this task, not later, or test runs will be confusing.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_auth.py -v` green after the assertion updates.

### UPDATE `backend/api/admin.py` — `require_role("manager")` → `require_role("admin")`
- **IMPLEMENT**: Single-line rename at line 31 (`_require_manager = require_role("admin")` and rename the variable to `_require_admin`). Update all three route handlers.
- **VALIDATE**: `uv run pytest backend/tests/integration -k admin -v` green. Manual: as `manager@org` (now no admin grant), `GET /v1/admin/queue` returns 403; as `admin@org`, 200.

### ADD error types to `backend/core/errors.py`
- **IMPLEMENT**: `InvalidToken(http_status=401, error_code="INVALID_TOKEN")`, `RevokedApiKey(http_status=401, error_code="REVOKED_API_KEY")`, `MissingScope(http_status=403, error_code="MISSING_SCOPE")`.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_errors.py -v` green; add one assertion per new code.

### CREATE `backend/core/auth/providers/oidc.py`
- **IMPLEMENT**: `OidcIdentityProvider` with constructor `(settings)` storing tenant + client_id + admin_group_id + jwks_url. `async def resolve(request)`:
  1. Extract `Authorization: Bearer <jwt>`; raise `Unauthorized("missing bearer token")` if absent.
  2. Decode unverified header → `kid`. Fetch JWKS (cached in-process for 1h, lazy refresh on `kid` miss).
  3. Verify signature, `iss == https://login.microsoftonline.com/{tenant}/v2.0`, `aud == client_id`, `exp`, `nbf`. Raise `InvalidToken` on any failure.
  4. Extract `preferred_username` or `email`; extract `groups: list[str]`. If `admin_group_id in groups` → `roles=["user","admin"]`, else `["user"]`.
  5. Return `User(email=..., roles=...)`.
- **PATTERN**: Mirror existing async style; use `httpx.AsyncClient` for JWKS (already a dep).
- **GOTCHA**: Entra's `groups` claim drops out if the user is in >150 groups — replaced with `_claim_names.groups` indirect claim or omitted entirely. For M1, document this; the mitigation is to use **app roles** in the App Registration (recommended) which emits a bounded `roles` claim instead. Plan keeps `groups` per the user's explicit request but the OIDC provider must log a `WARN` if neither claim is present.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_auth_oidc.py -v` — covers happy, tampered signature, wrong `aud`, expired, missing `groups` (falls back to `user` role with warning).

### CREATE `backend/core/auth/providers/saml.py`
- **IMPLEMENT**: Class `SamlIdentityProvider(IdentityProvider)` with `async def resolve(request): raise NotImplementedError("SAML provider lands in a future milestone — see .agents/plans/m1-azure-deployment-and-auth.md NOTES")`. Class exists so the abstraction stays honest and `select_provider("saml")` returns something concrete.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_auth.py::test_saml_provider_raises -v` green.

### CREATE `backend/core/telemetry.py`
- **IMPLEMENT**: `def configure_telemetry(settings)`: if `not settings.appinsights_connection_string: return`. Otherwise: set `OTEL_SERVICE_NAME=skillhub-{role}` (role=api|worker, taken from `OTEL_SERVICE_ROLE` env), call `configure_azure_monitor(connection_string=...)`, then `FastAPIInstrumentor.instrument()`, `HTTPXClientInstrumentor.instrument()`, `RedisInstrumentor.instrument()`. Idempotent via module-level flag.
- **GOTCHA**: `FastAPIInstrumentor.instrument()` is global; call it before any `FastAPI(...)` instance is created or call `instrument_app(app)` after. Choose the latter — cleaner.
- **VALIDATE**: With empty conn string, app boots silently. With a fake conn string (`InstrumentationKey=00000000-...`), no exception at startup.

### UPDATE `backend/core/config.py`
- **IMPLEMENT**: Add fields: `entra_tenant_id: str = ""`, `entra_client_id: str = ""`, `entra_group_id_admin: str = ""`, `appinsights_connection_string: str = ""`, `apikey_pepper: str = "dev-pepper-do-not-use-in-prod"`. Validator: if `auth_mode == "oidc"` and any of the three Entra fields is empty, raise `ValueError` on settings construction.
- **GOTCHA**: Validator must run at app boot, not lazily, or misconfigured prod silently degrades.
- **VALIDATE**: Unit test sets `AUTH_MODE=oidc` with empty `ENTRA_TENANT_ID` → `Settings()` raises.

### UPDATE `backend/app.py`
- **IMPLEMENT**: At top of `lifespan`: `configure_telemetry(settings)` then `instrument_app(app)` (if telemetry active). Build identity provider: `app.state.identity_provider = select_provider(settings)`. Build api_keys container handle (Phase 3 dependency).
- **VALIDATE**: `/healthz` still returns OK on local stack with `AUTH_MODE=stub`.

### UPDATE `backend/core/cosmos.py`
- **IMPLEMENT**: Add `API_KEYS_CONTAINER = "api_keys"` and `ensure_containers` creates it with PK `/key_id`.
- **VALIDATE**: `uv run pytest backend/tests/integration/test_cosmos_bootstrap.py -v` updated to assert 4 containers.

### CREATE `backend/models/api_key.py`
- **IMPLEMENT**: `ApiKeyDoc(BaseModel)` with `id`, `key_id`, `name`, `scopes: list[Literal["catalog:read","usage:write"]]`, `hash_sha256: str`, `created_by: str`, `created_at: datetime`, `revoked_at: datetime | None`, `last_used_at: datetime | None`. `ApiKeyIssueRequest(name, scopes)`. `ApiKeyIssueResponse(key_id, name, scopes, raw_key)` — raw_key returned exactly once. `ApiKeyListItem` — never includes the hash or the raw key.
- **VALIDATE**: Round-trip a fixture through `model_validate` + `model_dump`.

### CREATE `backend/core/auth/api_keys.py`
- **IMPLEMENT**: `_hash(raw, pepper) -> str` (SHA-256 hex). `async def issue(...)`, `async def revoke(key_id, ...)`, `async def resolve_api_key(raw_token, *, api_keys, redis, settings) -> ServiceAccount`. Resolution order: check Redis cache `cache:apikey:{first_8_hash_chars}` (Cosmos-fallback per rule #2); on miss query Cosmos `SELECT * FROM c WHERE c.hash_sha256 = @h AND IS_NULL(c.revoked_at)`; on hit cache for 60s (rule #3); update `last_used_at` *asynchronously* (fire-and-forget Cosmos patch — do not block the request).
- **GOTCHA**: Do NOT store the raw key anywhere — only the hash. The `ApiKeyIssueResponse` is the only time the raw key exists outside the caller's memory.
- **VALIDATE**: `uv run pytest backend/tests/unit/test_api_keys.py -v` and `backend/tests/integration/test_apikey_cache_fallback.py -v`.

### CREATE `backend/services/api_keys.py` and `backend/api/api_keys.py`
- **IMPLEMENT**: Service wraps `issue`/`revoke` with audit (`apikey_issue`, `apikey_revoke` actions; `skill_id` field carries `f"apikey:{key_id}"` so audit container partitioning still works). Router: `POST /v1/admin/api-keys` returns `ApiKeyIssueResponse` (201), `GET /v1/admin/api-keys` returns `list[ApiKeyListItem]`, `DELETE /v1/admin/api-keys/{key_id}` returns 204. Admin-only via `require_role("admin")`.
- **VALIDATE**: Integration test as admin issues a key, lists it (no raw key visible), revokes it, attempts to resolve raw_key → `RevokedApiKey`.

### UPDATE `backend/core/auth/deps.py` — `get_principal`
- **IMPLEMENT**: Per the pattern above. Bearer prefix dispatch: `sh_live_` → API key, else JWT. Stub mode short-circuits to user provider only.
- **VALIDATE**: Integration test hits one endpoint with each auth shape and asserts the recorded audit `actor` format (`alice@org` vs `svc:<key_id>`).

### UPDATE `backend/api/skills.py` — accept `Principal`
- **IMPLEMENT**: Change `list_skills`, `get_skill`, `download_skill` to `principal: Principal = Depends(get_principal)`. Do NOT change `uploads.py` or `admin.py` — those stay human-only via `get_current_user` / `require_role("admin")`.
- **VALIDATE**: Existing catalog tests pass; new test confirms an API-key-bearing request can list and download.

### CREATE `backend/workers/classifier.py` telemetry hook
- **IMPLEMENT**: At top of `main()`: `os.environ.setdefault("OTEL_SERVICE_ROLE", "worker"); configure_telemetry(get_settings())`. The worker is **not** a FastAPI app — only HTTPX + Redis instrumentation matters; do not call `FastAPIInstrumentor`.
- **VALIDATE**: Worker boots and processes one job with telemetry disabled (no regression). With a fake conn string, OTel emits a startup log line.

### CREATE `infra/main.bicep` + modules
- **IMPLEMENT**: Per file list above. `main.bicep` parameters: `env string`, `location string = resourceGroup().location`. Composes modules; centralizes outputs (App Service hostname, App Insights conn string, Key Vault URI). Naming via shared `var prefix = 'skillhub-${env}-${location}'`.
- **GOTCHA**: Cosmos `Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments` requires both the data-plane RBAC definition ID and the principal ID; cross-resource refs in Bicep are nontrivial — keep RBAC in `rbac.bicep` module that takes principalIds as parameters.
- **GOTCHA**: Key Vault soft-delete + purge protection on prod is irreversible; double-check `purgeProtectionEnabled: true` only for prod.
- **VALIDATE**: `az bicep lint --file infra/main.bicep` zero errors. `az deployment group what-if -g rg-skillhub-dev -f infra/main.bicep -p infra/parameters/dev.bicepparam` produces a clean diff against an empty RG.

### CREATE `.github/workflows/ci.yml`
- **IMPLEMENT**: Triggers `push`, `pull_request`. Jobs:
  - `backend`: `services:` block spins up `mcr.microsoft.com/cosmosdb/linux/azure-cosmos-emulator`, `mcr.microsoft.com/azure-storage/azurite`, `redis:7` — same images as docker-compose. `uv sync`, `ruff check`, `pyright`, `pytest backend/tests/unit`, `pytest backend/tests/integration -m integration`.
  - `frontend`: `pnpm install`, `pnpm --filter frontend lint`, `pnpm --filter frontend typecheck`, `pnpm --filter frontend build`.
  - `bicep`: `az bicep build` + `az deployment group what-if` against `rg-skillhub-dev` (requires OIDC login).
- **GOTCHA**: Cosmos emulator startup on a GH-hosted runner takes 60–120s; bump `services.cosmos.options` healthcheck retries accordingly. If runtime turns out flaky, fall back to a `setup-cosmos-emulator` action or run integration tests only on `main`.
- **VALIDATE**: `act -j backend` locally (if installed) reproduces the run. On first push, CI passes.

### CREATE `.github/workflows/deploy.yml`
- **IMPLEMENT**: `workflow_dispatch.inputs.env: choice(dev, staging, prod)`. `permissions: id-token: write, contents: read` for OIDC. One job per artifact, all depending on `bicep-deploy`. `environment: ${{ inputs.env }}` so GitHub enforces approvals per env. Use `azure/login@v2` with `auth-type: IDENTITY`, then `az deployment group create`, then `az webapp deploy --src-path artifact.zip`, then `az staticwebapp deploy ...`.
- **GOTCHA**: Federated credential subject must match exactly — `repo:<org>/<repo>:environment:dev` (note `environment:`, not `ref:`). One credential per env.
- **VALIDATE**: Dispatch to dev; deploy completes; `/healthz` on the deployed API reports `ok: true` for all three storage layers.

### CREATE `.github/workflows/rotate-key.yml`
- **IMPLEMENT**: `workflow_dispatch.inputs.{env, secret_name}`. Maps `secret_name` to the right `az ... regenerate-key` command (cosmos / redis / storage), reads the new value, `az keyvault secret set`, `az webapp restart` so App Service picks up the new Key Vault reference value.
- **VALIDATE**: Run against dev for `cosmos-key`; new key is in Key Vault; app keeps serving after restart.

### UPDATE `frontend/lib/api/client.ts` + add MSAL wiring
- **IMPLEMENT**: Per pattern above. Add `MsalProvider` wrapping in `app/layout.tsx`. Gate by `NEXT_PUBLIC_AUTH_MODE`. Stub path is untouched for local dev.
- **GOTCHA**: `acquireTokenSilent` throws `InteractionRequiredAuthError` on first call — must catch and fall back to `acquireTokenPopup` (or redirect). Provide a wrapper that handles this once.
- **VALIDATE**: Local stub mode: `pnpm --filter frontend dev` still works against the local backend. Against deployed dev: sign in with a real Entra account, upload a skill, see status flip.

### UPDATE `backend/tests/conftest.py`
- **IMPLEMENT**: Add fixtures `as_user(email="alice@org")` and `as_admin()` that override `app.state.identity_provider` with an in-memory provider returning the configured `User`. Existing `X-User-Email` test code paths can either keep working (stub mode) or migrate to the new fixture.
- **VALIDATE**: All M0 integration tests pass unchanged after the refactor.

---

## TESTING STRATEGY

### Unit Tests
- `test_auth.py` — Stub provider behavior, role mapping, package re-exports stable.
- `test_auth_oidc.py` — JWKS-cached verification, claim mapping, every error path (`InvalidToken`).
- `test_role_mapping.py` — Group claim → role table, missing groups claim falls back with WARN.
- `test_api_keys.py` — Issue/hash/resolve/revoke.
- `test_errors.py` — New error codes serialize correctly.
- `test_telemetry.py` — `configure_telemetry("")` is a no-op; `configure_telemetry("InstrumentationKey=...")` does not raise.

### Integration Tests (require docker-compose stack)
- `test_principal_dep.py` — Same route resolves user vs service account based on header.
- `test_apikey_cache_fallback.py` — Pause Redis mid-test; key resolves from Cosmos (rule #2).
- `test_apikey_flow.py` — Admin issues → caller uses → admin revokes → caller gets 401.
- `test_admin_role_renamed.py` — `manager@org` (no admin grant) → 403 on `/v1/admin/queue`; `admin@org` → 200.
- Update existing M0 integration tests to use the new `as_admin()` fixture.

### Cloud Smoke Tests (run after deploy.yml against dev)
- `tests/smoke/test_deployed_healthz.py` — Hits `https://api-skillhub-dev-eus.azurewebsites.net/healthz`, asserts all three layers OK.
- `tests/smoke/test_deployed_oidc.py` — Uses a service-principal JWT (client-credentials flow against the same Entra app) to call `GET /v1/skills`.
- `tests/smoke/test_deployed_apikey.py` — Issues a key via admin endpoint, calls catalog with it, revokes it.

### Edge Cases
- Token issued by *another* tenant → `InvalidToken` (issuer mismatch).
- Token whose `aud` is a different App Registration → `InvalidToken`.
- API key issued, revoked, then re-resolved within the cache TTL → must still 401 (revoke invalidates cache).
- Cosmos data-plane RBAC misconfigured → app boots but first read returns 403 from Cosmos; surfaced clearly in `/healthz`.
- Key Vault reference fails to resolve (wrong MI grant) → App Service `appSetting` shows `null`; settings validator must raise a clear error at boot, not at first request.
- Federated credential subject typo → `az login` step in CI fails with a recognizable error; CI must not silently fall back to PAT or env-var secrets.
- Two parallel `deploy.yml` runs against the same env → second one blocks on the GitHub environment lock (no concurrency races).
- Worker restarted mid-classification → unchanged M0 behavior (BLPOP + Cosmos durability); telemetry adds trace continuity.

---

## VALIDATION COMMANDS

Execute every command in order. Treat any non-zero exit as a stop.

### Level 1: Syntax & Style
```bash
uv run ruff format --check .
uv run ruff check .
pnpm --filter frontend lint
pnpm --filter frontend typecheck
az bicep lint --file infra/main.bicep
```

### Level 2: Unit Tests
```bash
uv run pytest backend/tests/unit -v
pnpm --filter frontend test --if-present
```

### Level 3: Integration Tests (local stack)
```bash
docker compose up -d
python scripts/wait_for_emulators.py
uv run pytest backend/tests/integration -v -m integration
```

### Level 4: Infra Plan
```bash
az login
az deployment group what-if -g rg-skillhub-dev \
  -f infra/main.bicep -p infra/parameters/dev.bicepparam
```

### Level 5: Cloud Deploy + Smoke (dev only, gated)
```bash
gh workflow run deploy.yml -f env=dev
gh run watch
uv run pytest tests/smoke -v --base-url https://api-skillhub-dev-eus.azurewebsites.net
```

### Level 6: Manual Validation
1. Visit the deployed frontend URL; click "Sign in"; complete Entra flow as a normal user; confirm `/admin/queue` is not in the nav (or 403s if accessed by URL).
2. Sign in as a user in the admin group; confirm `/admin/queue` loads and approve/reject work.
3. `curl -H "Authorization: Bearer <api-key>" https://api-skillhub-dev-eus.azurewebsites.net/v1/skills | jq` returns the catalog.
4. Open Application Insights → Transaction search; find a recent request; confirm distributed trace shows `Frontend pageView → API request → Cosmos dependency → Redis dependency`.
5. Run `gh workflow run rotate-key.yml -f env=dev -f secret_name=cosmos-key`. After workflow completes, `/healthz` still returns `cosmos: ok`.

---

## ACCEPTANCE CRITERIA

- [ ] `backend/core/auth/` package exports `User`, `Role`, `Principal`, `ServiceAccount`, `get_current_user`, `get_principal`, `require_role` — all existing imports keep working.
- [ ] `Role` is `Literal["user", "admin"]`; `manager` is fully removed from public role names.
- [ ] `require_role("admin")` gates `/v1/admin/*`; `require_role("user")` (or just `get_current_user`) gates upload + my-submissions; catalog reads accept either a `User` or a `ServiceAccount` via `get_principal`.
- [ ] `IdentityProvider` Protocol exists; `select_provider(settings)` returns `Stub`, `Oidc`, or `Saml` (the last raises `NotImplementedError` deliberately). Adding SAML is a one-file change in `backend/core/auth/providers/`.
- [ ] OIDC provider validates Entra JWTs (signature, iss, aud, exp, nbf) against the tenant JWKS, with caching; admin role is derived from `ENTRA_GROUP_ID_ADMIN` in the `groups` claim, mapped server-side.
- [ ] API key issuance returns the raw key exactly once; storage is hash-only; revocation is a soft flag; resolution uses Redis cache + Cosmos fallback (rule #2 verified by test with Redis paused).
- [ ] Every Redis key set by M1 code paths has a TTL (rule #3 verified by unit test); no M1 code writes to Redis before Cosmos (rule #1 verified by ordering test on API-key issue).
- [ ] Audit row `actor` is `email` for human callers and `svc:<key_id>` for machine callers — asserted in integration test.
- [ ] Bicep `what-if` against an empty `rg-skillhub-dev` produces a clean, deterministic plan; `az deployment group create` succeeds; `/healthz` on the deployed API returns `ok: true`.
- [ ] App Service references every secret via Key Vault (`@Microsoft.KeyVault(SecretUri=...)`); zero raw secret values in `appSettings`; verified by `az webapp config appsettings list | grep -v Microsoft.KeyVault | grep -iE 'key|secret|conn'` returning nothing sensitive.
- [ ] App Service + Function App system-assigned managed identities have `Key Vault Secrets User` on the vault; verified by reading at least one Key Vault reference value through `/healthz`.
- [ ] Application Insights shows a connected end-to-end trace from a frontend page view → API request → Cosmos dependency span, in the dev environment.
- [ ] Worker emits OTel spans into App Insights `dependencies` for Cosmos reads and Redis BLPOPs.
- [ ] GitHub Actions `ci.yml` runs on every PR with the docker-compose stack as service containers; required to merge.
- [ ] `deploy.yml` is `workflow_dispatch`, uses environment protection (dev=none, staging=1 reviewer, prod=2 reviewers), authenticates to Azure via OIDC federated credentials (no stored client secrets in GitHub).
- [ ] `rotate-key.yml` rotates a Cosmos key end-to-end against dev with zero downtime visible to a polling client.
- [ ] Local dev loop (`docker compose up && AUTH_MODE=stub`) still works with no Azure spend (AGENTS.md §6 not regressed).
- [ ] No deletion code paths anywhere in the API-key flow (revoke = soft flag, AGENTS.md §5 invariant preserved by analogy).
- [ ] All `Level 1`–`Level 4` validation commands pass; `Level 5` passes in dev environment.

---

## COMPLETION CHECKLIST

- [ ] All tasks completed in order
- [ ] Each task validation passed immediately
- [ ] All validation commands executed successfully
- [ ] Full backend test suite passes (unit + integration)
- [ ] Frontend lint + typecheck + build pass
- [ ] Bicep `what-if` clean against all three envs
- [ ] CI workflow green on a PR branch before merge
- [ ] Deploy workflow succeeded for dev; smoke tests green
- [ ] Manual OIDC sign-in walked through end-to-end in dev
- [ ] Manual API-key issue → use → revoke walked through end-to-end in dev
- [ ] App Insights end-to-end trace screenshotted and attached to the PR
- [ ] Rotation workflow exercised on dev for `cosmos-key`
- [ ] Staging deploy approved + completed; smoke tests green
- [ ] Prod deploy approved by two reviewers + completed; smoke tests green
- [ ] No secrets committed (verified by `gh secret list` showing only `AZURE_*` OIDC entries and grep over the repo)
- [ ] AGENTS.md updated only if a convention changed (otherwise leave alone)
- [ ] README updated with the cloud topology and a "deploy to your own Azure" quickstart

---

## NOTES

**Design decisions / trade-offs**

- **Two-role collapse is the right call now, not later.** Doing it in M1 means Entra group mappings, audit row shapes, and frontend role gates all use the final names from day one in cloud. Renaming `manager` → `admin` after real Entra groups are bound to it is a multi-team coordination headache. Cost is a one-time test sweep.
- **`IdentityProvider` Protocol > class hierarchy.** Protocol keeps providers independently testable, avoids inheritance traps, and lets the stub provider stay zero-dependency for offline tests. Adding SAML in a later milestone is literally a new file under `providers/` plus an `AUTH_MODE=saml` env value.
- **API keys are a *separate* auth path, not "users with no email".** Conflating them makes audit, scoping, and revocation muddled. The `Principal` union is the unifying type; routes opt in to either-or-both by depending on `get_principal` (both) vs `get_current_user` (humans only) vs `require_admin` (humans-with-admin only).
- **Key Vault references > KV SDK calls at runtime.** Less code, no SDK auth to debug at boot, automatic refresh on App Service restart. The one downside — settings can be `null` if the reference fails to resolve — is mitigated by the `Settings` validator that fails loudly at startup.
- **OIDC federated credentials > stored client secrets in GitHub.** Zero long-lived secrets. Per-env federated credentials enforce environment isolation: a dev-scoped workflow physically cannot mint a prod token.
- **Worker on App Service for M1, Functions for M4+.** Simpler ops; same Bicep pattern as the API; trivial to swap later when scale demands it. Documented in `infra/README.md`.
- **App roles vs group claims.** The plan honors the user's explicit request for group claims, but documents the app-roles alternative inline. If Entra returns the dreaded "too many groups" overflow in practice, switching to app roles is a one-config-toggle migration in the App Registration plus a 5-line change in `oidc.py` (claim path `groups` → `roles`).
- **`actor = "svc:<key_id>"` convention** keeps the audit container's single string column meaningful for both humans and machines without a schema migration. M2 curator already reads audit by `skill_id` partition, not by `actor`, so this is non-breaking.
- **Local dev stays on the stub.** Real OIDC against a dev Entra tenant from `localhost:3000` requires a redirect URI registration and a non-trivial sign-in flow; for a dev loop optimizing for "edit code → reload → see result in <2s", the stub wins. M1 keeps both paths functional.
- **Cosmos data-plane RBAC vs key-based auth.** Plan ships *key-based* in dev/staging via Key Vault references for speed, then switches *prod* to `DefaultAzureCredential` + RBAC via a small `cosmos.py` provider tweak. Rationale: ops gets the security win where it counts; iteration speed stays high where it doesn't.
- **TTL on JWKS cache is in-process, not Redis.** 1-hour TTL, refreshed lazily on `kid` miss. Adding Redis here would buy nothing — the cache is small, the keys rotate rarely, and per-process duplication is fine.

**Risks**
- **Cosmos emulator stability in GitHub Actions** is the single biggest CI risk. Mitigation: pin the image, generous healthcheck retries, fallback plan to run integration tests only on `main` if the runner times out repeatedly.
- **Entra "too many groups" overflow** can silently break admin grants for power users. Mitigation: warn-log in OIDC provider when neither `groups` nor `roles` claim is present; document the app-roles migration path.
- **Cosmos data-plane RBAC** has well-known propagation latency (up to ~5 minutes after assignment). First deploy will look broken; document this in `infra/README.md`.

**Confidence**: 7/10 that an execution agent can land this in one pass. The Bicep + GitHub OIDC federated credential + Entra App Registration triangle is the most likely source of friction — each one is straightforward, but together they have many small misconfiguration modes that only fail at deploy time. Recommended: land Phases 1–4 (backend + frontend, no Azure) in one PR and merge; land Phases 5–7 (infra + cloud) in a second PR after a dev Entra tenant and the three resource groups exist. The split keeps PR sizes reviewable and isolates code-level risk from infra-level risk.
