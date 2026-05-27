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

// Seed secret names (values are populated post-deploy by the rotate-key workflow
// or — for the Entra coordinates — by `azd provision` reading the matching
// .bicepparam values and the rotate workflow honoring them on first run).
//
// No `entra-client-secret`: the SPA is a public client (MSAL PKCE redirect) and
// the backend validates JWTs via JWKS — no confidential-client flow exists.
//
// The entra-* names are public coordinates (tenant id, app reg client ids,
// security group id, API scope). They live in KV anyway so we have one
// rotation surface for everything Entra-related and so the deploy workflow
// doesn't have to round-trip them through GH-Actions job outputs (which
// auto-redact any value matching a repo secret like AZURE_TENANT_ID).
var secretNames = [
  'cosmos-key'
  'blob-connection-string'
  'redis-primary-key'
  'appinsights-connection-string'
  'apikey-pepper'
  'entra-tenant-id'
  'entra-client-id'           // backend API app reg
  'entra-spa-client-id'       // frontend SPA app reg
  'entra-group-id-admin'
  'entra-api-scope'           // api://<api-app-id>/access_as_user
  // M5 — notifier worker reads this. Real value written by main.bicep from
  // the ACS module's `connectionString` output (the seed is a placeholder
  // overwritten on deploy).
  'acs-connection-string'
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
