using '../main.bicep'

param env = 'dev'
param location = 'eastus'
param authMode = 'oidc'
// Populate these from `scripts/setup-entra.sh dev <frontend-hostname>` output:
param entraTenantId = ''
param entraClientId = ''
param entraSpaClientId = ''
param entraGroupIdAdmin = ''

// --- M4 additions.

// Object IDs of Entra groups that get cluster-admin via AAD integration.
// Typically the same as `entraGroupIdAdmin` — we want app admins to be
// cluster admins for hotfix access. Leave [] to disable AAD admin (you'll
// need `--admin` credentials via the cluster-admin certificate, fine for dev).
param aadAdminGroupObjectIds = []

// Container Insights workspace. Leave empty to disable OMS agent in dev.
param logAnalyticsWorkspaceId = ''
