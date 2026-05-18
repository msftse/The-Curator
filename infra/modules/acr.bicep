// Azure Container Registry — single registry shared by all envs is acceptable
// at our scale (image tags carry the env, and AcrPull is granted to each
// cluster's kubelet identity). Per-env registries would force cross-RG
// replication and a more expensive Premium SKU; not worth the cost yet.
//
// Premium SKU is required for geo-replication and private endpoints.
// We keep `sku` parameterised so dev can run Standard.

@description('Resource name prefix (e.g. skillhub-dev-eastus).')
param prefix string

@description('Azure region.')
param location string = resourceGroup().location

@description('ACR SKU. Premium recommended for prod (geo-replication, private endpoints).')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Standard'

@description('Object ID of the kubelet UAMI to grant AcrPull. AKS uses this identity to pull images.')
param kubeletPrincipalId string

@description('Whether to allow public access. Set false in prod after a private endpoint is wired.')
param publicNetworkAccess bool = true

// ACR names must be globally unique, alphanumeric, 5-50 chars. Strip dashes.
var acrName = toLower(replace(replace('${prefix}acr', '-', ''), '_', ''))

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: skuName
  }
  properties: {
    adminUserEnabled: false  // Workload identity / AcrPull only — no admin user.
    publicNetworkAccess: publicNetworkAccess ? 'Enabled' : 'Disabled'
    // Zone redundancy on Premium only; default off for cost.
    zoneRedundancy: 'Disabled'
    // Soft-delete + retention to recover accidentally-deleted images. 7d
    // default; bump to 30 in prod via parameter if/when needed.
    policies: {
      retentionPolicy: {
        status: 'enabled'
        days: 7
      }
      // Quarantine policy is a Premium feature — leaving disabled.
    }
  }
}

// Grant the kubelet identity AcrPull on this registry. With this assignment
// in place no imagePullSecret is needed on any Pod spec — the kubelet pulls
// images using its own MI identity.
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'  // AcrPull built-in

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, kubeletPrincipalId, 'acr-pull')
  scope: acr
  properties: {
    principalId: kubeletPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalType: 'ServicePrincipal'
  }
}

output loginServer string = acr.properties.loginServer
output registryName string = acr.name
output registryId string = acr.id
