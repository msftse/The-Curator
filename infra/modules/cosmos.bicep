// Cosmos DB for NoSQL — system of record per AGENTS.md §3.
// Serverless in dev for cheap iteration; Standard provisioned in staging/prod.

@description('Resource name prefix (e.g. skillhub-dev-eastus).')
param prefix string

@description('Azure region.')
param location string

@description('Cosmos capacity mode. Serverless is good for dev; Standard for staging/prod.')
@allowed([
  'Serverless'
  'Standard'
])
param capacityMode string = 'Serverless'

@description('Cosmos database name.')
param databaseName string = 'skillhub'

var accountName = 'cosmos-${prefix}'

var capabilities = capacityMode == 'Serverless' ? [
  {
    name: 'EnableServerless'
  }
] : []

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: capabilities
    publicNetworkAccess: 'Enabled'
    minimalTlsVersion: 'Tls12'
  }
}

resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// Containers — PRD §10 plus the M1 `api_keys` container.
var containers = [
  {
    name: 'skills'
    pk: '/skill_id'
    defaultTtl: -1
  }
  {
    name: 'audit'
    pk: '/skill_id'
    defaultTtl: -1
  }
  {
    name: 'usage_events'
    pk: '/skill_id'
    defaultTtl: 7776000  // 90 days
  }
  {
    name: 'api_keys'
    pk: '/key_id'
    defaultTtl: -1
  }
]

resource containerResources 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = [for c in containers: {
  parent: db
  name: c.name
  properties: {
    resource: {
      id: c.name
      partitionKey: {
        paths: [c.pk]
        kind: 'Hash'
      }
      defaultTtl: c.defaultTtl
    }
  }
}]

output accountName string = account.name
output endpoint string = account.properties.documentEndpoint
output accountId string = account.id
output databaseName string = databaseName
