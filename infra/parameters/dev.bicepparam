using '../main.bicep'

param env = 'dev'
param location = 'eastus'
param authMode = 'oidc'
// Populate these from `scripts/setup-entra.sh dev <frontend-hostname>` output:
param entraTenantId = ''
param entraClientId = ''
param entraSpaClientId = ''
param entraGroupIdAdmin = ''
