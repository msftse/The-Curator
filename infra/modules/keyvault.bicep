// Key Vault — RBAC mode. All app secrets live here, referenced from App Service.

@description('Resource name prefix.')
param prefix string

@description('Azure region.')
param location string

@description('Whether to enable purge protection (IRREVERSIBLE — only enable for prod).')
param enablePurgeProtection bool = false

@description('Tenant ID for RBAC scope.')
param tenantId string = subscription().tenantId

var vaultName = take('kv-${prefix}', 24)

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: vaultName
  location: location
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// Seed secret names (values are populated post-deploy by the rotate-key workflow).
// No `entra-client-secret`: the SPA is a public client (MSAL PKCE redirect) and
// the backend validates JWTs via JWKS — no confidential-client flow exists.
var secretNames = [
  'cosmos-key'
  'blob-connection-string'
  'redis-primary-key'
  'appinsights-connection-string'
  'apikey-pepper'
]

resource seededSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for n in secretNames: {
  parent: kv
  name: n
  properties: {
    value: 'PLACEHOLDER_REPLACE_VIA_ROTATE_KEY_WORKFLOW'
    contentType: 'text/plain'
  }
}]

output vaultName string = kv.name
output vaultUri string = kv.properties.vaultUri
output vaultId string = kv.id
