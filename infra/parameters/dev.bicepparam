using '../main.bicep'

param env = 'dev'
param location = readEnvironmentVariable('AZURE_LOCATION', 'eastus2')
param authMode = readEnvironmentVariable('AUTH_MODE', 'oidc')

// Entra coordinates come from the tenant-specific setup. Run
// `scripts/setup-entra.sh dev <frontend-hostname>` and export the four
// values it prints as env vars (or set them in your `azd` env) before
// `azd provision`. We deliberately do NOT commit real tenant IDs.
//
// Required for authMode=oidc; safe to leave blank for stub/fake_oidc dev.
param entraTenantId      = readEnvironmentVariable('ENTRA_TENANT_ID', '')
param entraClientId      = readEnvironmentVariable('ENTRA_CLIENT_ID', '')
param entraSpaClientId   = readEnvironmentVariable('ENTRA_SPA_CLIENT_ID', '')
param entraGroupIdAdmin  = readEnvironmentVariable('ENTRA_GROUP_ID_ADMIN', '')

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
