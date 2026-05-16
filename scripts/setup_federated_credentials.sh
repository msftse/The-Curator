#!/usr/bin/env bash
# Register GitHub Actions OIDC federated credentials on an Entra App Registration.
#
# Usage:
#   scripts/setup_federated_credentials.sh <app-object-id> <env>
#
# Example:
#   scripts/setup_federated_credentials.sh 00000000-0000-0000-0000-000000000000 dev
#
# Prereqs:
#   - az login (with rights to update the App Registration)
#   - gh auth login (only to read the repo slug; can also hardcode REPO below)

set -euo pipefail

APP_OBJECT_ID="${1:?usage: $0 <app-object-id> <env>}"
ENV="${2:?usage: $0 <app-object-id> <env>}"

REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
ISSUER="https://token.actions.githubusercontent.com"
AUDIENCE="api://AzureADTokenExchange"
SUBJECT="repo:${REPO}:environment:${ENV}"
NAME="github-${ENV}"

echo "Adding federated credential:"
echo "  app:     $APP_OBJECT_ID"
echo "  repo:    $REPO"
echo "  env:     $ENV"
echo "  subject: $SUBJECT"

az ad app federated-credential create \
  --id "$APP_OBJECT_ID" \
  --parameters "{
    \"name\": \"$NAME\",
    \"issuer\": \"$ISSUER\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"$AUDIENCE\"],
    \"description\": \"GitHub Actions OIDC for $REPO ($ENV)\"
  }"

echo "Done. Set these GitHub repo secrets:"
echo "  AZURE_CLIENT_ID       = <app's client id>"
echo "  AZURE_TENANT_ID       = <directory tenant id>"
echo "  AZURE_SUBSCRIPTION_ID = <subscription containing rg-skillhub-$ENV>"
