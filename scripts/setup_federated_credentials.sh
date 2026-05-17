#!/usr/bin/env bash
# Provision a User-Assigned Managed Identity dedicated to GitHub Actions
# CI/CD for a given environment, federate it to the repo, and grant it the
# Azure roles deploy-aks.yml needs.
#
# Usage:
#   scripts/setup_federated_credentials.sh <env> [<resource-group>]
#
# Example:
#   scripts/setup_federated_credentials.sh dev
#   scripts/setup_federated_credentials.sh dev rg-dev
#
# Prereqs:
#   - az login (with Owner or RBAC Admin on the target RG)
#   - gh auth login (to read the repo slug, or set REPO=<owner>/<name>)
#   - The target RG already exists (e.g. via `azd up`)
#
# Why a UAMI instead of an App Registration?
#   - Clean separation: the SPA/API app regs identify *users*, this UAMI
#     identifies *CI*. Conflating them means anyone who can log into the
#     hub also has deploy rights — wrong.
#   - UAMIs don't need a client secret (federated OIDC only).
#   - Scoped roles: Contributor on the env's RG, AcrPush on the ACR,
#     AKS Cluster User + RBAC Cluster Admin on the AKS, Key Vault Secrets
#     Officer on the KV. No subscription-wide rights.

set -euo pipefail

ENV="${1:?usage: $0 <env> [<resource-group>]}"
RG="${2:-rg-${ENV}}"
LOCATION="$(az group show -n "$RG" --query location -o tsv)"
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
UAMI_NAME="id-skillhub-${ENV}-github"

echo "Setting up CI identity:"
echo "  env:     $ENV"
echo "  rg:      $RG ($LOCATION)"
echo "  repo:    $REPO"
echo "  uami:    $UAMI_NAME"
echo

# 1) UAMI (idempotent — `az identity create` is upsert).
az identity create \
  --name "$UAMI_NAME" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --query "{clientId:clientId, principalId:principalId, id:id}" \
  -o json > /tmp/${UAMI_NAME}.json

CLIENT_ID=$(jq -r .clientId /tmp/${UAMI_NAME}.json)
PRINCIPAL_ID=$(jq -r .principalId /tmp/${UAMI_NAME}.json)

# 2) Federated credentials. Two subjects:
#    - environment:<env>  used by jobs that declare `environment: <env>`
#    - ref:refs/heads/main used by jobs without an environment (e.g. the
#      resolve-env job in deploy-aks.yml on a push-to-main trigger)
for FC in \
  "github-${ENV}|repo:${REPO}:environment:${ENV}" \
  "github-${ENV}-main|repo:${REPO}:ref:refs/heads/main"; do
  NAME="${FC%%|*}"
  SUBJECT="${FC##*|}"
  # Best-effort idempotency — federated-credential create errors on duplicate.
  az identity federated-credential create \
    --name "$NAME" \
    --identity-name "$UAMI_NAME" \
    --resource-group "$RG" \
    --issuer "https://token.actions.githubusercontent.com" \
    --subject "$SUBJECT" \
    --audiences "api://AzureADTokenExchange" \
    -o none 2>/dev/null || echo "  (fed-cred $NAME already exists, skipping)"
  echo "  fed-cred: $NAME -> $SUBJECT"
done

# 3) RBAC. Each scoped to the minimum resource the workflow touches.
RG_ID=$(az group show -n "$RG" --query id -o tsv)
ACR_ID=$(az acr list -g "$RG" --query "[0].id" -o tsv)
AKS_ID=$(az aks list -g "$RG" --query "[0].id" -o tsv)
KV_ID=$(az keyvault list -g "$RG" --query "[0].id" -o tsv)

for ASSIGNMENT in \
  "Contributor|$RG_ID" \
  "AcrPush|$ACR_ID" \
  "Azure Kubernetes Service Cluster User Role|$AKS_ID" \
  "Azure Kubernetes Service RBAC Cluster Admin|$AKS_ID" \
  "Key Vault Secrets Officer|$KV_ID"; do
  ROLE="${ASSIGNMENT%%|*}"
  SCOPE="${ASSIGNMENT##*|}"
  if [[ -z "$SCOPE" || "$SCOPE" == "None" ]]; then
    echo "  (skipping $ROLE — no matching resource in $RG yet)"
    continue
  fi
  az role assignment create \
    --assignee-object-id "$PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role "$ROLE" \
    --scope "$SCOPE" \
    -o none 2>/dev/null || echo "  ($ROLE already assigned, skipping)"
  echo "  rbac:     $ROLE on $(basename "$SCOPE")"
done

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

echo
echo "Done. Set these GitHub repo secrets (gh secret set):"
echo "  AZURE_CLIENT_ID       = $CLIENT_ID"
echo "  AZURE_TENANT_ID       = $TENANT_ID"
echo "  AZURE_SUBSCRIPTION_ID = $SUB_ID"
echo
echo "And these GitHub environment ($ENV) variables (gh variable set --env $ENV):"
echo "  FRONTEND_HOST = <hostname e.g. agentic-curator.com>"
echo "  BACKEND_HOST  = <hostname e.g. api.agentic-curator.com>"
