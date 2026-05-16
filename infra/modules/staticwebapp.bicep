// Azure Static Web App for the Next.js frontend. Per-env deploy via SWA CLI.

@description('Resource name prefix.')
param prefix string

@description('Azure region for the SWA (Static Web Apps regional set is limited).')
param location string

@allowed([
  'Free'
  'Standard'
])
param skuName string = 'Free'

var swaName = 'swa-${prefix}'

resource swa 'Microsoft.Web/staticSites@2023-12-01' = {
  name: swaName
  location: location
  tags: {
    'azd-service-name': 'web'
  }
  sku: {
    name: skuName
    tier: skuName
  }
  properties: {
    buildProperties: {
      appLocation: 'frontend'
      apiLocation: ''
      outputLocation: '.next'
    }
  }
}

output swaName string = swa.name
output defaultHostname string = swa.properties.defaultHostname
