using '../main.bicep'

param env = 'prod'
param location = readEnvironmentVariable('AZURE_LOCATION', 'eastus')
param authMode = readEnvironmentVariable('AUTH_MODE', 'oidc')

// Entra coordinates — set in the azd env or shell before `azd provision`.
// See infra/parameters/dev.bicepparam for the full list of env vars.
param entraTenantId      = readEnvironmentVariable('ENTRA_TENANT_ID', '')
param entraClientId      = readEnvironmentVariable('ENTRA_CLIENT_ID', '')
param entraSpaClientId   = readEnvironmentVariable('ENTRA_SPA_CLIENT_ID', '')
param entraGroupIdAdmin  = readEnvironmentVariable('ENTRA_GROUP_ID_ADMIN', '')

// --- M4 additions.

// AAD groups granted cluster-admin via AAD integration. REQUIRED in prod
// because `disableLocalAccounts: true` is set (no cluster-admin certificate
// fallback). At least one group is mandatory — provision via
// `scripts/setup-entra.sh` and copy the group object ID here.
param aadAdminGroupObjectIds = []

// Container Insights workspace. REQUIRED in prod for alerting + retention.
// Provision separately or via a future infra/modules/loganalytics.bicep.
param logAnalyticsWorkspaceId = ''

// BYO App Gateway for AGIC. REQUIRED in prod (the addon mode is dev/staging
// only). Provision the App Gateway in a separate stack (cert from Key Vault,
// WAF v2 policy, public IP, vnet integration) and reference it here.
param agicMode = 'byo'
param agicAppGatewayId = ''

// ACR Premium SKU enables geo-replication + private endpoints (M5).
param acrSku = 'Premium'
