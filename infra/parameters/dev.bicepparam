using '../main.bicep'

param env = 'dev'
param location = 'eastus'
param authMode = 'oidc'
// Populate these from `scripts/setup-entra.sh dev <frontend-hostname>` output:
param entraTenantId = 'REDACTED-TENANT-ID'
param entraClientId = 'REDACTED-API-CLIENT-ID'
param entraSpaClientId = 'REDACTED-SPA-CLIENT-ID'
param entraGroupIdAdmin = 'REDACTED-ADMIN-GROUP-ID'

// --- M4 additions.

// Object IDs of Entra groups that get cluster-admin via AAD integration.
// Typically the same as `entraGroupIdAdmin` — we want app admins to be
// cluster admins for hotfix access. Leave [] to disable AAD admin (you'll
// need `--admin` credentials via the cluster-admin certificate, fine for dev).
param aadAdminGroupObjectIds = []

// Container Insights workspace. Leave empty to disable OMS agent in dev.
param logAnalyticsWorkspaceId = ''

// AKS version override. main.bicep default is 1.30.5 which is now LTS-only;
// pin a current standard-support version here. Bump as Azure deprecates.
param kubernetesVersion = '1.34.7'
