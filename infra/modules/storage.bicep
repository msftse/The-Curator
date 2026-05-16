// Storage account + Blob containers for bundle artifacts, archive, and snapshots.

@description('Resource name prefix.')
param prefix string

@description('Azure region.')
param location string

@description('Whether to deny public network access (true for prod).')
param denyPublicAccess bool = false

// Account names: alphanumeric only, <=24 chars. Strip dashes from prefix.
var accountName = take(replace('st${prefix}', '-', ''), 24)

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: accountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    publicNetworkAccess: denyPublicAccess ? 'Disabled' : 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 30
    }
  }
}

var containerNames = [
  'published'
  'archive'
  'snapshots'
  'staging'
]

resource containers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for n in containerNames: {
  parent: blobService
  name: n
  properties: {
    publicAccess: 'None'
  }
}]

output accountName string = storage.name
output accountId string = storage.id
output blobEndpoint string = storage.properties.primaryEndpoints.blob
