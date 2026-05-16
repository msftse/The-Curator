using '../main.bicep'

param env = 'staging'
param location = 'eastus'
param authMode = 'oidc'
param entraTenantId = ''
param entraClientId = ''
param entraSpaClientId = ''
param entraGroupIdAdmin = ''

// --- M4 additions.

// AAD groups granted cluster-admin. Recommend the same group as
// `entraGroupIdAdmin` for consistency between app and infra admin sets.
param aadAdminGroupObjectIds = []

// Container Insights workspace. Highly recommended for staging.
param logAnalyticsWorkspaceId = ''
