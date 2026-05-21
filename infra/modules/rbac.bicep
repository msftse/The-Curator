// RBAC role assignments — per-UAMI grants to Key Vault, Cosmos, and Blob.
//
// Authorization model (mirrors AGENTS.md §3 storage split):
//
//   frontend         — KV Secrets User ONLY, to let the CSI driver mount the
//                      public Entra coordinates (tenant id, SPA client id,
//                      api scope) on the pod's behalf. The pod process itself
//                      never holds a KV token. No Cosmos / Blob / Redis grants.
//   backend          — KV Secrets User, Cosmos Data Contributor,
//                      Blob Data Contributor, Blob Delegator (mints
//                      user-delegation SAS for catalog downloads).
//   classifier       — KV Secrets User (Foundry key), Cosmos Data Contributor,
//                      Blob Data Contributor (reads bundles, writes
//                      classification metadata).
//   curator          — KV Secrets User, Cosmos Data Contributor,
//                      Blob Data Contributor, Blob Delegator (snapshot +
//                      restore flows mint signed URLs identically to
//                      the backend's catalog path).
//   backend-k8s-jobs — no Azure RBAC. Permissions are K8s-side (Role /
//                      RoleBinding in the helm chart, scoped to
//                      `create jobs` on the `curator-ondemand` CronJob
//                      template, namespace `skillhub`).
//
// Cosmos data-plane RBAC is gated on `assignCosmosDataPlane` per the
// original infra design: dev/staging keep key-based auth for simpler
// emulator parity, prod uses RBAC. Propagation latency up to ~5 min
// (documented in infra/README.md).

@description('Key Vault name.')
param keyVaultName string

@description('Cosmos DB account name.')
param cosmosAccountName string

@description('Storage account name.')
param storageAccountName string

@description('Frontend UAMI principal ID.')
param frontendPrincipalId string

@description('Backend UAMI principal ID.')
param backendPrincipalId string

@description('Classifier UAMI principal ID.')
param classifierPrincipalId string

@description('Curator UAMI principal ID.')
param curatorPrincipalId string

@description('Defender UAMI principal ID (M5).')
param defenderPrincipalId string = ''

@description('Notifier UAMI principal ID (M5).')
param notifierPrincipalId string = ''

@description('ACS resource ID for notifier role scoping (M5). Empty = skip ACS RBAC.')
param acsResourceId string = ''

@description('Whether to assign Cosmos data-plane RBAC. Prod=true, dev/staging=false (key-based).')
param assignCosmosDataPlane bool = false

// Built-in role definition IDs (subscription-scoped).
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'  // Key Vault Secrets User
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
// Storage Blob Delegator — grants `generateUserDelegationKey/action`, the
// permission required to mint user-delegation SAS tokens. Data Contributor
// alone does NOT include this action. Required by:
//   - backend GET /v1/skills/{id}/download_url  (signed catalog downloads)
//   - curator snapshot + restore code paths in backend/core/blob.py
var storageBlobDelegatorRoleId = 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'  // Storage Blob Delegator
// M5 — Defender reads bundle bytes from blob (any container; we keep it at
// Blob Data Contributor parity with classifier/backend for simplicity in v1).
// Cognitive Services User on the AI Services account is granted out-of-band
// the same way the classifier already does (FOUNDRY_DEPLOYMENT side).
// M5 — Notifier sends email via ACS. The built-in role is "Contributor" on
// the ACS resource for v1 (Azure has no dedicated "ACS Sender" data-plane
// role today — the SMS/Email send is gated by the connection string, which
// the notifier reads from KV). Document & revisit when ACS publishes a
// granular role. KV Secrets User covers the connection-string read.
// Graph `GroupMember.Read.All` is an Entra app permission, NOT an Azure RBAC
// role — admin-consented via setup-entra.sh extension (M5-5); not auto-granted
// here by design.
var acsContributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'  // Contributor (placeholder until ACS gets a data role)

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosAccountName
}
resource acs 'Microsoft.Communication/communicationServices@2023-04-01' existing = if (!empty(acsResourceId)) {
  name: last(split(acsResourceId, '/'))
}

// Components that get the full data-plane grant. Frontend is intentionally
// absent; backend-k8s-jobs is K8s-only and not represented here. Defender
// (M5) joins the data plane: it reads bundles, writes the defender report
// back to Cosmos, and (via the backend on quarantine) needs blob read on
// uploads + blob write on quarantine.
var dataPlaneComponents = empty(defenderPrincipalId) ? [
  {
    name: 'backend'
    principalId: backendPrincipalId
  }
  {
    name: 'classifier'
    principalId: classifierPrincipalId
  }
  {
    name: 'curator'
    principalId: curatorPrincipalId
  }
] : [
  {
    name: 'backend'
    principalId: backendPrincipalId
  }
  {
    name: 'classifier'
    principalId: classifierPrincipalId
  }
  {
    name: 'curator'
    principalId: curatorPrincipalId
  }
  {
    name: 'defender'
    principalId: defenderPrincipalId
  }
]

// Key Vault Secrets User on the vault for each data-plane component.
resource kvAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for c in dataPlaneComponents: {
  name: guid(kv.id, c.principalId, 'kv-secrets-user')
  scope: kv
  properties: {
    principalId: c.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}]

// Frontend KV grant — separate from the data-plane loop because the frontend
// is intentionally excluded from Cosmos / Blob / Redis (AGENTS.md §3 keeps
// all data-plane reads behind the backend). It gets KV access only so the
// CSI driver can mount the public Entra coordinates (tenant id, SPA client
// id, api scope) on the frontend pod's behalf. These values are emitted as
// `window.__ENV__` to the browser — they're public coordinates, not secrets.
resource frontendKvAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, frontendPrincipalId, 'kv-secrets-user')
  scope: kv
  properties: {
    principalId: frontendPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

// Storage Blob Data Contributor for each data-plane component.
resource blobAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for c in dataPlaneComponents: {
  name: guid(storage.id, c.principalId, 'blob-data-contributor')
  scope: storage
  properties: {
    principalId: c.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}]

// Storage Blob Delegator — separate from Data Contributor (the latter
// covers only data operations, NOT the `generateUserDelegationKey/action`
// control-plane verb required to mint user-delegation SAS). All three
// data-plane components get it: backend (catalog downloads), classifier
// (no user it would mint SAS for today, but harmless and consistent),
// curator (snapshot + restore signed URLs).
resource blobDelegatorAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for c in dataPlaneComponents: {
  name: guid(storage.id, c.principalId, 'blob-delegator')
  scope: storage
  properties: {
    principalId: c.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDelegatorRoleId)
    principalType: 'ServicePrincipal'
  }
}]

// Cosmos data-plane RBAC (Built-in Data Contributor). Prod-only by default.
var cosmosDataContributorDefId = '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'

resource cosmosAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = [for c in dataPlaneComponents: if (assignCosmosDataPlane) {
  parent: cosmos
  name: guid(cosmos.id, c.principalId, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: cosmosDataContributorDefId
    principalId: c.principalId
    scope: cosmos.id
  }
}]

output assignmentCount int = length(dataPlaneComponents)

// ---------------------------------------------------------------------------
// M5 — Notifier UAMI role assignments.
//
// Notifier is intentionally NOT in `dataPlaneComponents`: it does not read
// Cosmos directly, does not read/write Blob, and does not need Blob
// Delegator. It only needs:
//   1. Key Vault Secrets User (read `acs-connection-string`).
//   2. Contributor on the ACS resource (placeholder until ACS publishes a
//      dedicated send-only data role).
//   3. Microsoft Graph `GroupMember.Read.All` — Entra app permission, NOT
//      Azure RBAC. Granted out-of-band via setup-entra.sh extension in M5-5.
//      Intentionally not auto-granted here so tenant-admin consent remains
//      explicit (plan §12 risk #1).
// ---------------------------------------------------------------------------

resource notifierKvAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(notifierPrincipalId)) {
  name: guid(kv.id, notifierPrincipalId, 'kv-secrets-user')
  scope: kv
  properties: {
    principalId: notifierPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}

resource notifierAcsAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(notifierPrincipalId) && !empty(acsResourceId)) {
  name: guid(acsResourceId, notifierPrincipalId, 'acs-sender')
  scope: acs
  properties: {
    principalId: notifierPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acsContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}
