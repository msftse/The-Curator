#!/bin/bash
# Local helm dry-run + install reproduction of deploy-aks.yml helm job.
# Source: derived from the latest azd deployment outputs in rg-dev.
# Usage: bash scripts/helm-deploy-dev.sh [--dry-run|--install|--upgrade]
set -euo pipefail

ENV=${ENV:-dev}
RG="rg-$ENV"
MODE=${1:---dry-run}

# Discover latest azd deployment with our outputs
DEP=$(az deployment group list -g "$RG" \
  --query "reverse(sort_by([?properties.provisioningState=='Succeeded'], &properties.timestamp))[].name" \
  -o tsv | while read N; do
    HAS=$(az deployment group show -g "$RG" -n "$N" \
      --query "properties.outputs.acrLoginServer.value" -o tsv 2>/dev/null || true)
    if [[ -n "$HAS" ]]; then echo "$N"; break; fi
done)
echo "Using deployment: $DEP"
az deployment group show -g "$RG" -n "$DEP" --query properties.outputs -o json > /tmp/outs.json

OUTS=$(cat /tmp/outs.json)
v() { echo "$OUTS" | jq -r ".$1.value // empty"; }

# Fallbacks for fields that may not yet exist in stale deployments.
BLOB_URL=$(v blobAccountUrl)
if [[ -z "$BLOB_URL" ]]; then
  BLOB_URL=$(az storage account show -n "$(v storageAccount)" -g "$RG" --query primaryEndpoints.blob -o tsv)
fi
REDIS_HOST=$(v redisHost)
if [[ -z "$REDIS_HOST" ]]; then
  REDIS_HOST=$(az redis list -g "$RG" --query "[0].hostName" -o tsv)
fi
ENTRA_SCOPE="api://$(v entraClientId)/access_as_user"

# Hostnames from GH env vars equivalent
FRONTEND_HOST=${FRONTEND_HOST:-agentic-curator.com}
BACKEND_HOST=${BACKEND_HOST:-api.agentic-curator.com}

ACR=$(v acrLoginServer)
IMAGE_TAG=${IMAGE_TAG:-$(git rev-parse HEAD)}

ACTION="template"
HELM_FLAGS="--debug"
case "$MODE" in
  --dry-run)  ACTION="template"; HELM_FLAGS="";;
  --install)  ACTION="upgrade --install"; HELM_FLAGS="--atomic --wait --timeout=10m";;
  --upgrade)  ACTION="upgrade"; HELM_FLAGS="--atomic --wait --timeout=10m";;
esac

echo "Action: $ACTION  Image tag: $IMAGE_TAG"

# shellcheck disable=SC2086
helm $ACTION skillhub charts/agentic-skill-hub \
  --namespace skillhub --create-namespace \
  --values charts/agentic-skill-hub/values-${ENV}.yaml \
  --set image.tag="${IMAGE_TAG}" \
  --set global.azureTenantId="$(v entraTenantId)" \
  --set global.imageRegistry="${ACR}" \
  --set global.workloadIdentity.frontendClientId="$(v frontendUamiClientId)" \
  --set global.workloadIdentity.backendClientId="$(v backendUamiClientId)" \
  --set global.workloadIdentity.classifierClientId="$(v classifierUamiClientId)" \
  --set global.workloadIdentity.curatorClientId="$(v curatorUamiClientId)" \
  --set global.workloadIdentity.backendK8sJobsClientId="$(v backendK8sJobsUamiClientId)" \
  --set global.workloadIdentityObjectIds.backend="$(v backendUamiPrincipalId)" \
  --set global.workloadIdentityObjectIds.classifier="$(v classifierUamiPrincipalId)" \
  --set global.workloadIdentityObjectIds.curator="$(v curatorUamiPrincipalId)" \
  --set keyVault.name="$(v keyVaultName)" \
  --set keyVault.tenantId="$(v entraTenantId)" \
  --set ingress.hosts.frontend="${FRONTEND_HOST}" \
  --set ingress.hosts.backend="${BACKEND_HOST}" \
  --set backend.env.ENTRA_TENANT_ID="$(v entraTenantId)" \
  --set backend.env.ENTRA_CLIENT_ID="$(v entraClientId)" \
  --set backend.env.ENTRA_GROUP_ID_ADMIN="$(v entraGroupIdAdmin)" \
  --set backend.env.COSMOS_ENDPOINT="$(v cosmosEndpoint)" \
  --set backend.env.COSMOS_DB_NAME="$(v cosmosDbName)" \
  --set backend.env.BLOB_ACCOUNT_URL="${BLOB_URL}" \
  --set backend.env.REDIS_HOST="${REDIS_HOST}" \
  --set backend.env.CORS_ORIGINS="https://${FRONTEND_HOST}" \
  --set-string backend.env.APPINSIGHTS_CONNECTION_STRING="$(v appInsightsConnectionString)" \
  --set classifier.env.COSMOS_ENDPOINT="$(v cosmosEndpoint)" \
  --set classifier.env.COSMOS_DB_NAME="$(v cosmosDbName)" \
  --set classifier.env.BLOB_ACCOUNT_URL="${BLOB_URL}" \
  --set classifier.env.REDIS_HOST="${REDIS_HOST}" \
  --set-string classifier.env.APPINSIGHTS_CONNECTION_STRING="$(v appInsightsConnectionString)" \
  --set curator.env.COSMOS_ENDPOINT="$(v cosmosEndpoint)" \
  --set curator.env.COSMOS_DB_NAME="$(v cosmosDbName)" \
  --set curator.env.BLOB_ACCOUNT_URL="${BLOB_URL}" \
  --set curator.env.REDIS_HOST="${REDIS_HOST}" \
  --set-string curator.env.APPINSIGHTS_CONNECTION_STRING="$(v appInsightsConnectionString)" \
  --set frontend.env.ENTRA_TENANT_ID="$(v entraTenantId)" \
  --set frontend.env.ENTRA_CLIENT_ID="$(v entraSpaClientId)" \
  --set frontend.env.ENTRA_API_SCOPE="${ENTRA_SCOPE}" \
  --set frontend.env.API_BASE="https://${BACKEND_HOST}" \
  $HELM_FLAGS
