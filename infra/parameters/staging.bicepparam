using '../main.bicep'

param env = 'staging'
param location = readEnvironmentVariable('AZURE_LOCATION', 'eastus')
param authMode = readEnvironmentVariable('AUTH_MODE', 'oidc')

// Entra coordinates — set in the azd env or shell before `azd provision`.
// See infra/parameters/dev.bicepparam for the full list of env vars.
param entraTenantId      = readEnvironmentVariable('ENTRA_TENANT_ID', '')
param entraClientId      = readEnvironmentVariable('ENTRA_CLIENT_ID', '')
param entraSpaClientId   = readEnvironmentVariable('ENTRA_SPA_CLIENT_ID', '')
param entraGroupIdAdmin  = readEnvironmentVariable('ENTRA_GROUP_ID_ADMIN', '')

// --- M4 additions.

// AAD groups granted cluster-admin. Recommend the same group as
// `entraGroupIdAdmin` for consistency between app and infra admin sets.
param aadAdminGroupObjectIds = []

// Container Insights workspace. Highly recommended for staging.
param logAnalyticsWorkspaceId = ''
