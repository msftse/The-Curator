// AKS cluster for the Agentic Skill Hub.
//
// Design choices:
//   - Azure CNI Overlay: pods get an overlay IP, nodes get VNet IPs. Best
//     IP density + native NetworkPolicy + private endpoint compatibility.
//   - Managed VNet (`networkProfile.podCidr` set, no `vnetSubnetID`): AKS
//     creates and owns the VNet/subnets. BYO VNet is a future hardening step.
//   - Workload identity + OIDC issuer enabled cluster-wide. Per-component
//     UAMIs + federated credentials are created in `identity.bicep`.
//   - Ingress: ingress-nginx, installed cluster-side via Helm. It fronts a
//     Kubernetes Service of type LoadBalancer (Azure managed LB) and is
//     not provisioned by this Bicep.
//   - Key Vault Secrets Provider add-on: CSI driver mounts KV secrets into
//     pods at `/mnt/secrets-store/`. Per-component SecretProviderClass
//     manifests in the Helm chart pin which secrets each pod sees.
//   - Container Insights add-on for OMS log forwarding to Log Analytics.

@description('Resource name prefix (e.g. skillhub-dev-eastus).')
param prefix string

@description('Azure region.')
param location string = resourceGroup().location

@description('Environment short name. Drives sizing defaults.')
@allowed([
  'dev'
  'staging'
  'prod'
])
param env string

@description('Kubernetes version. Pin to a known-good supported version.')
param kubernetesVersion string = '1.30.5'

@description('System pool VM SKU. 2-node baseline for AKS-managed pods (CoreDNS, metrics-server).')
param systemPoolVmSize string = 'Standard_D2s_v3'

@description('User pool VM SKU. Hosts application workloads.')
param userPoolVmSize string = env == 'prod' ? 'Standard_D4s_v3' : 'Standard_D2s_v3'

@description('User pool autoscale min count.')
param userPoolMinCount int = env == 'prod' ? 2 : 1

@description('User pool autoscale max count.')
param userPoolMaxCount int = env == 'prod' ? 10 : 5

@description('Log Analytics workspace ID for Container Insights. Pass empty to skip.')
param logAnalyticsWorkspaceId string = ''

@description('Admin group object IDs that get cluster-admin via AAD integration. Use the same admin group as Entra app role membership for consistency.')
param aadAdminGroupObjectIds array = []

// Cluster name must be ≤63 chars and DNS-safe. The prefix is already
// constrained by the parent template.
var clusterName = '${prefix}-aks'

// Overlay CIDRs — chosen to avoid the default RFC1918 ranges commonly used
// in corporate networks. Pods + services live in the overlay; nodes get
// VNet IPs from the AKS-managed VNet (10.224.0.0/16 by default).
var podCidr = '10.244.0.0/16'
var serviceCidr = '10.0.0.0/16'
var dnsServiceIp = '10.0.0.10'

resource aks 'Microsoft.ContainerService/managedClusters@2024-05-01' = {
  name: clusterName
  location: location
  identity: {
    // System-assigned identity. We DO NOT use this as the workload identity
    // for app pods — that's per-component UAMIs federated via OIDC. This
    // identity is what AKS uses to manage cluster resources (load balancer,
    // ACR pull *via kubelet*, etc.).
    type: 'SystemAssigned'
  }
  sku: {
    name: 'Base'
    tier: env == 'prod' ? 'Standard' : 'Free'  // Uptime SLA on prod only.
  }
  properties: {
    kubernetesVersion: kubernetesVersion
    dnsPrefix: clusterName
    enableRBAC: true

    // OIDC issuer + workload identity. Both required for federated UAMIs.
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }

    // Azure AD integration for `az aks get-credentials --admin` and for
    // RBAC against the cluster. Local accounts disabled in prod.
    aadProfile: {
      managed: true
      enableAzureRBAC: true
      adminGroupObjectIDs: aadAdminGroupObjectIds
      tenantID: subscription().tenantId
    }
    disableLocalAccounts: env == 'prod'

    // Azure CNI Overlay. The key combo is `networkPlugin: 'azure'` +
    // `networkPluginMode: 'overlay'`. NetworkPolicy is Cilium-based — best
    // perf and least operational overhead in 2025.
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkPolicy: 'cilium'
      networkDataplane: 'cilium'
      podCidr: podCidr
      serviceCidr: serviceCidr
      dnsServiceIP: dnsServiceIp
      loadBalancerSku: 'standard'
      outboundType: 'loadBalancer'
    }

    // Two node pools (one in the array, system; the user pool is created
    // below as a separate child resource so we can attach autoscale).
    agentPoolProfiles: [
      {
        name: 'system'
        mode: 'System'
        osType: 'Linux'
        osSKU: 'AzureLinux'
        vmSize: systemPoolVmSize
        count: 2
        // Only run AKS system components on the system pool. CriticalAddonsOnly
        // taint keeps app pods off.
        nodeTaints: [
          'CriticalAddonsOnly=true:NoSchedule'
        ]
        type: 'VirtualMachineScaleSets'
        availabilityZones: env == 'prod' ? ['1', '2', '3'] : null
        enableAutoScaling: false
        upgradeSettings: {
          maxSurge: '33%'
        }
      }
    ]

    addonProfiles: {
      // Key Vault Secrets Provider — CSI driver that mounts KV secrets.
      azureKeyvaultSecretsProvider: {
        enabled: true
        config: {
          enableSecretRotation: 'true'
          rotationPollInterval: '2m'
        }
      }
      // Container Insights (OMS agent). Optional — only wire if a workspace
      // is provided (caller passes empty string to skip).
      omsAgent: empty(logAnalyticsWorkspaceId) ? {
        enabled: false
      } : {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: logAnalyticsWorkspaceId
        }
      }
    }

    autoUpgradeProfile: {
      // Patch-level auto-upgrade only. Minor version bumps remain manual.
      upgradeChannel: 'patch'
      nodeOSUpgradeChannel: 'NodeImage'
    }
  }
}

// User node pool — separate resource so autoscale + Spot/standard split
// stays flexible without re-creating the whole cluster.
resource userPool 'Microsoft.ContainerService/managedClusters/agentPools@2024-05-01' = {
  parent: aks
  name: 'user'
  properties: {
    mode: 'User'
    osType: 'Linux'
    osSKU: 'AzureLinux'
    vmSize: userPoolVmSize
    type: 'VirtualMachineScaleSets'
    enableAutoScaling: true
    minCount: userPoolMinCount
    maxCount: userPoolMaxCount
    count: userPoolMinCount
    availabilityZones: env == 'prod' ? ['1', '2', '3'] : null
    upgradeSettings: {
      maxSurge: '33%'
    }
    // No taints — app workloads schedule freely here. KEDA-scaled classifier
    // pods rely on the autoscaler to grow this pool when queue depth spikes.
  }
}

// AKS exposes the kubelet identity for ACR pull grants. This is a
// system-managed UAMI created automatically when `oidcIssuerProfile` and
// `securityProfile.workloadIdentity` are enabled — read it back so the
// parent template can pass its principalId to acr.bicep.
output kubeletPrincipalId string = aks.properties.identityProfile.kubeletidentity.objectId
output kubeletClientId string = aks.properties.identityProfile.kubeletidentity.clientId

output clusterName string = aks.name
output clusterFqdn string = aks.properties.fqdn
output oidcIssuerUrl string = aks.properties.oidcIssuerProfile.issuerURL
output systemAssignedPrincipalId string = aks.identity.principalId

// CSI driver UAMI — needs Key Vault `get`/`list` on each per-env vault.
// Currently the helm chart's SecretProviderClass uses *workload identity*
// directly (the pod's UAMI accesses KV, not the CSI driver's), so this is
// surfaced for completeness only. If we ever switch to the CSI driver's
// own identity (the other supported pattern), grant secrets-user to this.
output csiSecretsProviderClientId string = aks.properties.addonProfiles.azureKeyvaultSecretsProvider.identity.clientId
