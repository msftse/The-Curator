using '../main.bicep'

param env = 'dev'
param location = 'eastus'
param authMode = 'oidc'
// Populate these from the App Registration created out of band:
param entraTenantId = ''
param entraClientId = ''
param entraGroupIdAdmin = ''
