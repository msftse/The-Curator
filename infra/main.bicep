// Agentic Skill Hub — top-level deployment.
//
// Composes Cosmos, Storage, Redis, Key Vault, App Insights, ACR, AKS,
// per-component UAMIs (frontend, backend, classifier, curator,
// backend-k8s-jobs), and RBAC role assignments. Secrets flow through Key
// Vault references via the AKS Secrets Provider CSI driver — zero raw
// secret values in pod env (AGENTS.md §8 + plan §5).
//
// `deployScope`:
//   - `data` deploys ONLY Cosmos, Storage, Redis. Use for early iteration
//     when the cluster isn't ready yet.
//   - `all`  deploys the full footprint (default for staging/prod).

@description('Environment short name (dev|staging|prod).')
@allowed([
  'dev'
  'staging'
  'prod'
])
param env string

@description('Deployment scope. `data` = Cosmos+Storage+Redis only; `all` = full footprint.')
@allowed([
  'data'
  'all'
])
param deployScope string = 'all'

@description('Azure region.')
param location string = resourceGroup().location

@description('Cosmos capacity mode.')
@allowed([
  'Serverless'
  'Standard'
])
param cosmosCapacityMode string = env == 'dev' ? 'Serverless' : 'Standard'

@description('Redis SKU.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param redisSku string = env == 'prod' ? 'Premium' : (env == 'staging' ? 'Standard' : 'Basic')

@description('Redis capacity.')
param redisCapacity int = env == 'prod' ? 1 : 0

@description('Enable AOF persistence on Redis (Premium only).')
param redisEnableAof bool = env == 'prod'

@description('Whether to enable Key Vault purge protection (IRREVERSIBLE — prod only).')
param enableKvPurgeProtection bool = env == 'prod'

@description('ACR SKU.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
// NOTE: Sub policy in MngEnvMCAP* doesn't permit Basic/Standard ACR — only
// Premium is allowed. Cost trade-off accepted in dev for the unblock.
param acrSku string = 'Premium'

@description('Kubernetes version. Bump as Azure deprecates; 1.30.5 became LTS-only in 2026-05.')
param kubernetesVersion string = '1.34.7'

@description('Cluster AAD admin group object IDs.')
param aadAdminGroupObjectIds array = []

@description('Log Analytics workspace ID for Container Insights. Empty to skip.')
param logAnalyticsWorkspaceId string = ''

// --- Auth params (still surfaced for the helm chart to consume via values).

@description('AUTH_MODE for the deployed pods.')
@allowed([
  'stub'
  'fake_oidc'
  'oidc'
])
param authMode string = 'oidc'

@description('Entra tenant ID (required when authMode=oidc).')
param entraTenantId string = ''

@description('Entra app client ID for the backend API registration.')
param entraClientId string = ''

@description('Entra app client ID for the frontend SPA registration.')
param entraSpaClientId string = ''

@description('Entra group ID for admin role.')
param entraGroupIdAdmin string = ''

var prefix = '${env}-${location}'
var fullPrefix = 'skillhub-${prefix}'
var deployAll = deployScope == 'all'

// --- Data plane.

module appi 'modules/appinsights.bicep' = if (deployAll) {
  name: 'appi'
  params: {
    prefix: fullPrefix
    location: location
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  params: {
    prefix: fullPrefix
    location: location
    capacityMode: cosmosCapacityMode
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    prefix: fullPrefix
    location: location
    denyPublicAccess: env == 'prod'
  }
}

module redis 'modules/redis.bicep' = {
  name: 'redis'
  params: {
    prefix: fullPrefix
    location: location
    skuName: redisSku
    capacity: redisCapacity
    enableAof: redisEnableAof
  }
}

module kv 'modules/keyvault.bicep' = if (deployAll) {
  name: 'kv'
  params: {
    prefix: fullPrefix
    location: location
    enablePurgeProtection: enableKvPurgeProtection
  }
}

// M5 — Azure Communication Services (notifier worker).
module acs 'modules/communication.bicep' = if (deployAll) {
  name: 'acs'
  params: {
    prefix: fullPrefix
    location: location
  }
}

resource acsResource 'Microsoft.Communication/communicationServices@2023-04-01' existing = if (deployAll) {
  name: acs!.outputs.acsName
}

// M5 — write ACS connection string into Key Vault, overwriting the seed
// placeholder set by keyvault.bicep. Notifier pod (M5-5) mounts this via
// the CSI driver.
resource acsConnSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (deployAll) {
  name: '${kv!.outputs.vaultName}/acs-connection-string'
  properties: {
    value: acsResource.listKeys().primaryConnectionString
    contentType: 'text/plain'
  }
  dependsOn: [
    kv
    acs
  ]
}

// --- Runtime plane (AKS + ACR + per-component UAMIs).

module aks 'modules/aks.bicep' = if (deployAll) {
  name: 'aks'
  params: {
    prefix: fullPrefix
    location: location
    env: env
    kubernetesVersion: kubernetesVersion
    logAnalyticsWorkspaceId: logAnalyticsWorkspaceId
    aadAdminGroupObjectIds: aadAdminGroupObjectIds
  }
}

module acr 'modules/acr.bicep' = if (deployAll) {
  name: 'acr'
  params: {
    prefix: fullPrefix
    location: location
    skuName: acrSku
    kubeletPrincipalId: aks!.outputs.kubeletPrincipalId
    publicNetworkAccess: env != 'prod'
  }
}

module identity 'modules/identity.bicep' = if (deployAll) {
  name: 'identity'
  params: {
    prefix: fullPrefix
    location: location
    oidcIssuerUrl: aks!.outputs.oidcIssuerUrl
    namespace: 'skillhub'
  }
}

module rbac 'modules/rbac.bicep' = if (deployAll) {
  name: 'rbac'
  params: {
    keyVaultName: kv!.outputs.vaultName
    cosmosAccountName: cosmos.outputs.accountName
    storageAccountName: storage.outputs.accountName
    frontendPrincipalId: identity!.outputs.frontendPrincipalId
    backendPrincipalId: identity!.outputs.backendPrincipalId
    classifierPrincipalId: identity!.outputs.classifierPrincipalId
    curatorPrincipalId: identity!.outputs.curatorPrincipalId
    defenderPrincipalId: identity!.outputs.defenderPrincipalId
    notifierPrincipalId: identity!.outputs.notifierPrincipalId
    assignCosmosDataPlane: env == 'prod'
  }
}

// --- Outputs (consumed by deploy-aks.yml workflow + helm values).

output clusterName string = deployAll ? aks!.outputs.clusterName : ''
output clusterFqdn string = deployAll ? aks!.outputs.clusterFqdn : ''
output oidcIssuerUrl string = deployAll ? aks!.outputs.oidcIssuerUrl : ''

output acrLoginServer string = deployAll ? acr!.outputs.loginServer : ''
output acrName string = deployAll ? acr!.outputs.registryName : ''

output keyVaultName string = deployAll ? kv!.outputs.vaultName : ''
output keyVaultUri string = deployAll ? kv!.outputs.vaultUri : ''
output cosmosAccount string = cosmos.outputs.accountName
output cosmosEndpoint string = cosmos.outputs.endpoint
output cosmosDbName string = cosmos.outputs.databaseName
output storageAccount string = storage.outputs.accountName
output blobAccountUrl string = storage.outputs.blobEndpoint
output redisHost string = redis.outputs.hostName
output redisSslPort int = redis.outputs.sslPort
output appInsightsConnectionString string = deployAll ? appi!.outputs.connectionString : ''

// Per-component UAMI client IDs — fed into the Helm chart as
// `serviceAccount.azure.workload.identity/client-id` annotations.
output frontendUamiClientId string = deployAll ? identity!.outputs.frontendClientId : ''
output backendUamiClientId string = deployAll ? identity!.outputs.backendClientId : ''
output classifierUamiClientId string = deployAll ? identity!.outputs.classifierClientId : ''
output curatorUamiClientId string = deployAll ? identity!.outputs.curatorClientId : ''
output backendK8sJobsUamiClientId string = deployAll ? identity!.outputs.backendK8sJobsClientId : ''

// M5 — defender + notifier UAMI client IDs (helm SA annotations).
output defenderUamiClientId string = deployAll ? identity!.outputs.defenderClientId : ''
output notifierUamiClientId string = deployAll ? identity!.outputs.notifierClientId : ''
output defenderUamiPrincipalId string = deployAll ? identity!.outputs.defenderPrincipalId : ''
output notifierUamiPrincipalId string = deployAll ? identity!.outputs.notifierPrincipalId : ''

// M5 — ACS outputs. `acsName` is exposed so operators can look up the
// generated managed-domain sender address post-deploy (it isn't available
// synchronously from Bicep). Connection string is NOT output — it lives in
// Key Vault only.
output acsName string = deployAll ? acs!.outputs.acsName : ''
output acsResourceId string = deployAll ? acs!.outputs.acsResourceId : ''

// Per-component UAMI principal (object) IDs — required when the chart
// configures AGENTS.md §3 Cosmos/Redis/Blob access via Entra (prod). The
// principal id is the subject of the Azure RBAC role assignments in
// `rbac.bicep`. Surfaced so the deploy workflow can `--set
// global.workloadIdentityObjectIds.*` on `helm upgrade`.
output backendUamiPrincipalId string = deployAll ? identity!.outputs.backendPrincipalId : ''
output classifierUamiPrincipalId string = deployAll ? identity!.outputs.classifierPrincipalId : ''
output curatorUamiPrincipalId string = deployAll ? identity!.outputs.curatorPrincipalId : ''

// Surfaced for the helm chart values:
output authMode string = authMode
output entraTenantId string = entraTenantId
output entraClientId string = entraClientId
output entraSpaClientId string = entraSpaClientId
output entraGroupIdAdmin string = entraGroupIdAdmin
// Derived scope name. The setup-entra.sh script always exposes the backend
// API scope as `access_as_user` via the app-id-uri form `api://{client-id}`
// (required by some tenant policies). Render it here so the chart values
// don't have to compose strings.
output entraApiScope string = empty(entraClientId) ? '' : 'api://${entraClientId}/access_as_user'
