// Agentic Skill Hub — top-level deployment.
//
// Composes Cosmos, Storage, Redis, Key Vault, App Insights, App Service (API),
// Worker App Service, Static Web App, and RBAC role assignments. All secrets
// flow through Key Vault references — zero raw secret values in appSettings
// (AGENTS.md §8 + plan §5).

@description('Environment short name (dev|staging|prod).')
@allowed([
  'dev'
  'staging'
  'prod'
])
param env string

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

@description('App Service plan SKU.')
param appSkuName string = env == 'prod' ? 'P1v3' : (env == 'staging' ? 'P1v3' : 'B1')

@description('Whether to enable Key Vault purge protection (IRREVERSIBLE — prod only).')
param enableKvPurgeProtection bool = env == 'prod'

@description('AUTH_MODE for the deployed API.')
@allowed([
  'stub'
  'fake_oidc'
  'oidc'
])
param authMode string = 'oidc'

@description('Entra tenant ID (required when authMode=oidc).')
param entraTenantId string = ''

@description('Entra app client ID.')
param entraClientId string = ''

@description('Entra group ID for admin role.')
param entraGroupIdAdmin string = ''

var prefix = '${env}-${location}'
var fullPrefix = 'skillhub-${prefix}'

module appi 'modules/appinsights.bicep' = {
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

module kv 'modules/keyvault.bicep' = {
  name: 'kv'
  params: {
    prefix: fullPrefix
    location: location
    enablePurgeProtection: enableKvPurgeProtection
  }
}

module api 'modules/appservice.bicep' = {
  name: 'api'
  params: {
    prefix: fullPrefix
    location: location
    skuName: appSkuName
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosDbName: cosmos.outputs.databaseName
    keyVaultUri: kv.outputs.vaultUri
    appInsightsConnectionString: appi.outputs.connectionString
    authMode: authMode
    entraTenantId: entraTenantId
    entraClientId: entraClientId
    entraGroupIdAdmin: entraGroupIdAdmin
  }
}

// Worker reuses the API plan id (cheaper in non-prod).
module worker 'modules/worker.bicep' = {
  name: 'worker'
  params: {
    prefix: fullPrefix
    location: location
    appServicePlanId: api.outputs.planId
    cosmosEndpoint: cosmos.outputs.endpoint
    cosmosDbName: cosmos.outputs.databaseName
    keyVaultUri: kv.outputs.vaultUri
    appInsightsConnectionString: appi.outputs.connectionString
  }
}

module swa 'modules/staticwebapp.bicep' = {
  name: 'swa'
  params: {
    prefix: fullPrefix
    location: location
  }
}

module rbac 'modules/rbac.bicep' = {
  name: 'rbac'
  params: {
    prefix: fullPrefix
    keyVaultName: kv.outputs.vaultName
    cosmosAccountName: cosmos.outputs.accountName
    storageAccountName: storage.outputs.accountName
    assignCosmosDataPlane: env == 'prod'
    principalIds: [
      api.outputs.principalId
      worker.outputs.principalId
    ]
  }
}

output apiHostname string = api.outputs.defaultHostName
output workerSite string = worker.outputs.siteName
output frontendHostname string = swa.outputs.defaultHostname
output keyVaultName string = kv.outputs.vaultName
output cosmosAccount string = cosmos.outputs.accountName
output storageAccount string = storage.outputs.accountName
output appInsightsName string = appi.outputs.appInsightsName
