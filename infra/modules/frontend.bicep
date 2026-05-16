// Alias for staticwebapp.bicep — plan refers to this filename.
// Kept as a re-export so callers can use either name.

@description('Resource name prefix.')
param prefix string

@description('Azure region.')
param location string

@allowed([
  'Free'
  'Standard'
])
param skuName string = 'Free'

module swa 'staticwebapp.bicep' = {
  name: 'swa-inner'
  params: {
    prefix: prefix
    location: location
    skuName: skuName
  }
}

output swaName string = swa.outputs.swaName
output defaultHostname string = swa.outputs.defaultHostname
