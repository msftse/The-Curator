// Linux App Service Plan + Web App for FastAPI.
// AppSettings pull every secret via Key Vault references.

@description('Resource name prefix.')
param prefix string

@description('Azure region.')
param location string

@description('App Service plan SKU (e.g. B1 dev, P1v3 prod).')
param skuName string = 'B1'

@description('Cosmos endpoint (non-secret).')
param cosmosEndpoint string

@description('Cosmos database name.')
param cosmosDbName string = 'skillhub'

@description('Key Vault URI (e.g. https://kv-skillhub-dev-eastus.vault.azure.net/).')
param keyVaultUri string

@description('App Insights connection string (non-secret value goes through KV reference).')
param appInsightsConnectionString string

@description('AUTH_MODE for this environment.')
@allowed([
  'stub'
  'fake_oidc'
  'oidc'
])
param authMode string = 'oidc'

@description('Entra tenant ID.')
param entraTenantId string = ''

@description('Entra app (client) ID.')
param entraClientId string = ''

@description('Entra group ID that maps to the admin role.')
param entraGroupIdAdmin string = ''

@description('Image name (linuxFxVersion) — e.g. PYTHON|3.12.')
param linuxFxVersion string = 'PYTHON|3.12'

var planName = 'plan-${prefix}'
var siteName = 'app-${prefix}'

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: skuName
  }
  properties: {
    reserved: true
  }
}

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: siteName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'WEBSITES_PORT'
          value: '8000'
        }
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmosEndpoint
        }
        {
          name: 'COSMOS_DB_NAME'
          value: cosmosDbName
        }
        {
          name: 'COSMOS_KEY'
          value: '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/cosmos-key/)'
        }
        {
          name: 'BLOB_CONNECTION_STRING'
          value: '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/blob-connection-string/)'
        }
        {
          name: 'REDIS_URL'
          value: '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/redis-primary-key/)'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'APIKEY_PEPPER'
          value: '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/apikey-pepper/)'
        }
        {
          name: 'AUTH_MODE'
          value: authMode
        }
        {
          name: 'ENTRA_TENANT_ID'
          value: entraTenantId
        }
        {
          name: 'ENTRA_CLIENT_ID'
          value: entraClientId
        }
        {
          name: 'ENTRA_GROUP_ID_ADMIN'
          value: entraGroupIdAdmin
        }
        {
          name: 'OTEL_SERVICE_ROLE'
          value: 'api'
        }
      ]
    }
  }
}

output siteName string = site.name
output defaultHostName string = site.properties.defaultHostName
output principalId string = site.identity.principalId
output planId string = plan.id
