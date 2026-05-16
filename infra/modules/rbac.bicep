// RBAC role assignments — per-UAMI grants to Key Vault, Cosmos, and Blob.
//
// Authorization model (mirrors AGENTS.md §3 storage split):
//
//   frontend         — no Azure data-plane access. All reads go through
//                      the backend. (No assignments.)
//   backend          — KV Secrets User, Cosmos Data Contributor,
//                      Blob Data Contributor (full system-of-record access).
//   classifier       — KV Secrets User (Foundry key), Cosmos Data Contributor,
//                      Blob Data Contributor (reads bundles, writes
//                      classification metadata).
//   curator          — KV Secrets User, Cosmos Data Contributor,
//                      Blob Data Contributor (snapshots, archives, restore).
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

@description('Backend UAMI principal ID.')
param backendPrincipalId string

@description('Classifier UAMI principal ID.')
param classifierPrincipalId string

@description('Curator UAMI principal ID.')
param curatorPrincipalId string

@description('Whether to assign Cosmos data-plane RBAC. Prod=true, dev/staging=false (key-based).')
param assignCosmosDataPlane bool = false

// Built-in role definition IDs (subscription-scoped).
var kvSecretsUserRoleId = '4633458b-17de-9032-9817-3b16e3a85e6a'  // Key Vault Secrets User
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' existing = {
  name: cosmosAccountName
}

// Components that get the full data-plane grant. Frontend is intentionally
// absent; backend-k8s-jobs is K8s-only and not represented here.
var dataPlaneComponents = [
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
