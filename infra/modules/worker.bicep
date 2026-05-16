// Classifier worker — deployed as a second Linux App Service per Phase decision.
// Same Key Vault reference pattern as the API. M4 can swap this for a Function App.

@description('Resource name prefix.')
param prefix string

@description('Azure region.')
param location string

@description('App Service plan ID to host the worker on (reuse the API plan in dev).')
param appServicePlanId string

@description('Cosmos endpoint.')
param cosmosEndpoint string

@description('Cosmos database name.')
param cosmosDbName string = 'skillhub'

@description('Key Vault URI.')
param keyVaultUri string

@description('App Insights connection string.')
param appInsightsConnectionString string

@description('Linux runtime version.')
param linuxFxVersion string = 'PYTHON|3.12'

var siteName = 'worker-${prefix}'

resource site 'Microsoft.Web/sites@2023-12-01' = {
  name: siteName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlanId
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appCommandLine: 'python -m backend.workers.classifier'
      appSettings: [
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
          name: 'REDIS_URL'
          value: '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/redis-primary-key/)'
        }
        {
          name: 'BLOB_CONNECTION_STRING'
          value: '@Microsoft.KeyVault(SecretUri=${keyVaultUri}secrets/blob-connection-string/)'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsightsConnectionString
        }
        {
          name: 'OTEL_SERVICE_ROLE'
          value: 'worker'
        }
      ]
    }
  }
}

output siteName string = site.name
output principalId string = site.identity.principalId
