// Per-component User-Assigned Managed Identities + federated credentials.
//
// Each of the four images (frontend, backend, classifier, curator) gets
// its own UAMI. A fifth UAMI (`backend-k8s-jobs`) is granted narrow RBAC
// on the cluster itself to let the backend create curator on-demand Jobs
// from the `curator-ondemand` CronJob template (AGENTS.md §5 second line).
//
// Each UAMI is federated to a K8s ServiceAccount via the AKS OIDC issuer.
// Workload identity wires it all up: when a Pod with the annotated SA
// requests an Azure token, the AKS-MSI sidecar exchanges the projected
// SA token for an Azure access token via federated credentials. No
// secrets, no service principals.
//
// Federated credential subject format:
//   `system:serviceaccount:<namespace>:<sa-name>`
// Namespace is `skillhub` for all five (the helm chart pins to this).

@description('Resource name prefix (e.g. skillhub-dev-eastus).')
param prefix string

@description('Azure region.')
param location string = resourceGroup().location

@description('OIDC issuer URL from the AKS cluster (aks.outputs.oidcIssuerUrl).')
param oidcIssuerUrl string

@description('K8s namespace where the SAs live. Helm chart MUST match.')
param namespace string = 'skillhub'

// One UAMI per component. Names are stable so federated credentials and
// RBAC assignments can reference them by name. The trailing `-uami` tag
// makes them easy to identify in the portal.
var components = [
  'frontend'
  'backend'
  'classifier'
  'curator'
  'backend-k8s-jobs'
]

resource uamis 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for c in components: {
  name: '${prefix}-${c}-uami'
  location: location
}]

// Federated credentials — one per UAMI, subject to the matching K8s SA.
// `audiences` MUST be exactly `['api://AzureADTokenExchange']` for AKS WI.
resource feds 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = [for (c, i) in components: {
  parent: uamis[i]
  name: '${c}-fedcred'
  properties: {
    issuer: oidcIssuerUrl
    subject: 'system:serviceaccount:${namespace}:${c}'
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}]

// Outputs are positional — keep in sync with the `components` array order.
// Parent template uses these to thread per-component principalId / clientId
// into rbac.bicep and helm values.
output frontendPrincipalId string = uamis[0].properties.principalId
output frontendClientId string = uamis[0].properties.clientId
output frontendName string = uamis[0].name

output backendPrincipalId string = uamis[1].properties.principalId
output backendClientId string = uamis[1].properties.clientId
output backendName string = uamis[1].name

output classifierPrincipalId string = uamis[2].properties.principalId
output classifierClientId string = uamis[2].properties.clientId
output classifierName string = uamis[2].name

output curatorPrincipalId string = uamis[3].properties.principalId
output curatorClientId string = uamis[3].properties.clientId
output curatorName string = uamis[3].name

output backendK8sJobsPrincipalId string = uamis[4].properties.principalId
output backendK8sJobsClientId string = uamis[4].properties.clientId
output backendK8sJobsName string = uamis[4].name
