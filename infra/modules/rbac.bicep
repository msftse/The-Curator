// RBAC role assignments — grants App Service + Worker MIs read access to Key Vault,
// data-plane RBAC on Cosmos, and Blob Data Contributor on Storage.

@description('Resource name prefix.')
param prefix string

@description('Key Vault name.')
param keyVaultName string

@description('Cosmos DB account name.')
param cosmosAccountName string

@description('Storage account name.')
param storageAccountName string

@description('Principal IDs to grant access to (App Service + Worker MIs).')
param principalIds array

@description('Whether to assign Cosmos data-plane RBAC. Prod uses RBAC; dev/staging stay key-based.')
param assignCosmosDataPlane bool = false

// Built-in role definition IDs.
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

// Key Vault Secrets User on the vault for each principal.
resource kvAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (pid, i) in principalIds: {
  name: guid(kv.id, pid, 'kv-secrets-user')
  scope: kv
  properties: {
    principalId: pid
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalType: 'ServicePrincipal'
  }
}]

// Storage Blob Data Contributor on the storage account for each principal.
resource blobAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (pid, i) in principalIds: {
  name: guid(storage.id, pid, 'blob-data-contributor')
  scope: storage
  properties: {
    principalId: pid
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalType: 'ServicePrincipal'
  }
}]

// Cosmos data-plane RBAC: Built-in Data Contributor.
// Only assigned in prod (per plan decision). Dev/staging stay key-based.
// Note: propagation latency up to ~5 minutes (documented in infra/README.md).
var cosmosDataContributorDefId = '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'

resource cosmosAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-05-15' = [for (pid, i) in principalIds: if (assignCosmosDataPlane) {
  parent: cosmos
  name: guid(cosmos.id, pid, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: cosmosDataContributorDefId
    principalId: pid
    scope: cosmos.id
  }
}]

output assignmentCount int = length(principalIds)
