#!/usr/bin/env bash
# Provision Entra ID app registrations + admin security group for Agentic Skill Hub.
#
# Creates three artifacts in the signed-in tenant:
#
#   1. Backend API app registration (`skillhub-api-<env>`)
#      - Exposes scope `access_as_user` on identifier URI `api://skillhub-<env>`.
#      - Group claims enabled (security groups → SecurityGroup) for admin mapping.
#      - Single-tenant.
#
#   2. Frontend SPA app registration (`skillhub-spa-<env>`)
#      - SPA platform redirect URIs: https://<frontend-hostname>/auth/callback
#        plus http://localhost:3000/auth/callback for local dev.
#      - Pre-authorized for the backend API's `access_as_user` scope, so
#        MSAL token acquisition does not prompt for consent.
#      - Group claims enabled (same reasoning).
#
#   3. Security group `skillhub-admins-<env>` (mailEnabled=false, securityEnabled=true).
#      - Membership in this group is the source of truth for the `admin` role.
#      - Pass the resulting object id as `ENTRA_GROUP_ID_ADMIN` to the API.
#
# All IDs are printed at the end in a copy-paste friendly block ready to drop
# into `infra/parameters/<env>.bicepparam` and the frontend `.env.production`.
#
# Idempotency: re-running with the same env updates redirect URIs and ensures
# the pre-authorization is present but does not duplicate registrations.
#
# Usage:
#   scripts/setup-entra.sh <env> [<frontend-hostname>]
#
# Examples:
#   scripts/setup-entra.sh dev skillhub-dev.example.com
#   scripts/setup-entra.sh dev -          # localhost-only (no prod redirect)
#
# Prereqs:
#   - az login (account with `Application Administrator` + `Groups Administrator`
#     roles in the target tenant; Global Admin works too).
#   - jq

set -euo pipefail

ENV="${1:?usage: $0 <env> [<frontend-hostname>]}"
FRONTEND_HOST="${2:--}"

API_APP_NAME="skillhub-api-${ENV}"
SPA_APP_NAME="skillhub-spa-${ENV}"
ADMIN_GROUP_NAME="skillhub-admins-${ENV}"
SCOPE_NAME="access_as_user"
SCOPE_ID="00000000-0000-0000-0000-000000000aaa"  # stable per env after first run.

SPA_REDIRECTS=("http://localhost:3000/auth/callback")
if [[ "$FRONTEND_HOST" != "-" && "$FRONTEND_HOST" != "localhost" ]]; then
  SPA_REDIRECTS+=("https://${FRONTEND_HOST}/auth/callback")
fi

TENANT_ID="$(az account show --query tenantId -o tsv)"

echo "==> Tenant: $TENANT_ID"
echo "==> Env:    $ENV"
echo "==> Front:  $FRONTEND_HOST"
echo

# -----------------------------------------------------------------------------
# 1. Backend API registration
# -----------------------------------------------------------------------------
echo "==> Ensuring backend API registration '$API_APP_NAME'..."
API_APP_JSON="$(az ad app list --display-name "$API_APP_NAME" --query '[0]' -o json)"
if [[ "$API_APP_JSON" == "null" || -z "$API_APP_JSON" ]]; then
  API_APP_JSON="$(az ad app create \
    --display-name "$API_APP_NAME" \
    --sign-in-audience AzureADMyOrg \
    -o json)"
  echo "    created."
else
  echo "    already exists."
fi
API_APP_ID="$(echo "$API_APP_JSON" | jq -r '.appId')"
API_OBJECT_ID="$(echo "$API_APP_JSON" | jq -r '.id')"
# Tenant policy here requires identifier URIs to contain the app id (or tenant
# verified domain). Using the app-id form is portable across tenants.
API_IDENTIFIER_URI="api://${API_APP_ID}"

# Identifier URI + access_as_user scope. The scope id is stable across runs so
# pre-authorized client lookups don't drift.
echo "==> Configuring API scope on $API_IDENTIFIER_URI ..."
az ad app update --id "$API_OBJECT_ID" \
  --identifier-uris "$API_IDENTIFIER_URI" \
  --set "api={
    \"oauth2PermissionScopes\": [
      {
        \"id\": \"$SCOPE_ID\",
        \"adminConsentDescription\": \"Allow the app to call the Skill Hub API as the signed-in user.\",
        \"adminConsentDisplayName\": \"Access Skill Hub as user\",
        \"userConsentDescription\": \"Allow this app to access Skill Hub on your behalf.\",
        \"userConsentDisplayName\": \"Access Skill Hub on your behalf\",
        \"isEnabled\": true,
        \"type\": \"User\",
        \"value\": \"$SCOPE_NAME\"
      }
    ]
  }" >/dev/null

# Emit group claims (SecurityGroup) so the JWT carries `groups` for the
# admin-role mapping. Skip the 150-groups overage with the optional-claims
# `groups` claim type.
echo "==> Enabling group claims (SecurityGroup) on API app..."
az ad app update --id "$API_OBJECT_ID" \
  --set "groupMembershipClaims=SecurityGroup" \
  --set "optionalClaims={
    \"idToken\": [{\"name\": \"groups\", \"essential\": false, \"additionalProperties\": []}],
    \"accessToken\": [{\"name\": \"groups\", \"essential\": false, \"additionalProperties\": []}]
  }" >/dev/null

# -----------------------------------------------------------------------------
# 2. Frontend SPA registration
# -----------------------------------------------------------------------------
echo "==> Ensuring frontend SPA registration '$SPA_APP_NAME'..."
SPA_APP_JSON="$(az ad app list --display-name "$SPA_APP_NAME" --query '[0]' -o json)"
if [[ "$SPA_APP_JSON" == "null" || -z "$SPA_APP_JSON" ]]; then
  SPA_APP_JSON="$(az ad app create \
    --display-name "$SPA_APP_NAME" \
    --sign-in-audience AzureADMyOrg \
    -o json)"
  echo "    created."
else
  echo "    already exists."
fi
SPA_APP_ID="$(echo "$SPA_APP_JSON" | jq -r '.appId')"
SPA_OBJECT_ID="$(echo "$SPA_APP_JSON" | jq -r '.id')"

# SPA platform redirect URIs. `az ad app update --web-redirect-uris` targets
# the wrong platform; SPA URIs must live under `.spa.redirectUris`.
echo "==> Setting SPA redirect URIs..."
REDIRECTS_JSON="$(printf '%s\n' "${SPA_REDIRECTS[@]}" | jq -R . | jq -s .)"
az ad app update --id "$SPA_OBJECT_ID" \
  --set "spa={\"redirectUris\": $REDIRECTS_JSON}" \
  --set "web={\"redirectUris\": []}" >/dev/null

# Pre-authorize the SPA for the backend API's access_as_user scope so users
# don't get a consent prompt every login. We PATCH the full api object via
# Microsoft Graph because `az ad app update --set api.preAuthorizedApplications`
# fails when the local cached object doesn't include the `api` key.
echo "==> Pre-authorizing SPA for $API_IDENTIFIER_URI/$SCOPE_NAME ..."
API_PATCH_BODY=$(cat <<JSON
{
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [
      {
        "id": "$SCOPE_ID",
        "adminConsentDescription": "Allow the app to call the Skill Hub API as the signed-in user.",
        "adminConsentDisplayName": "Access Skill Hub as user",
        "userConsentDescription": "Allow this app to access Skill Hub on your behalf.",
        "userConsentDisplayName": "Access Skill Hub on your behalf",
        "isEnabled": true,
        "type": "User",
        "value": "$SCOPE_NAME"
      }
    ],
    "preAuthorizedApplications": [
      {
        "appId": "$SPA_APP_ID",
        "delegatedPermissionIds": ["$SCOPE_ID"]
      }
    ]
  }
}
JSON
)
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$API_OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "$API_PATCH_BODY" >/dev/null

# Required resource access on the SPA side so MSAL can request the scope.
echo "==> Wiring SPA required-resource-access for the API..."
az ad app update --id "$SPA_OBJECT_ID" \
  --set "requiredResourceAccess=[
    {
      \"resourceAppId\": \"$API_APP_ID\",
      \"resourceAccess\": [
        {\"id\": \"$SCOPE_ID\", \"type\": \"Scope\"}
      ]
    }
  ]" >/dev/null

# Same group claims story as the API — needed so the SPA can decode the
# id_token and surface admin-only UI affordances.
az ad app update --id "$SPA_OBJECT_ID" \
  --set "groupMembershipClaims=SecurityGroup" \
  --set "optionalClaims={
    \"idToken\": [{\"name\": \"groups\", \"essential\": false, \"additionalProperties\": []}],
    \"accessToken\": [{\"name\": \"groups\", \"essential\": false, \"additionalProperties\": []}]
  }" >/dev/null

# -----------------------------------------------------------------------------
# 3. Admin security group
# -----------------------------------------------------------------------------
echo "==> Ensuring admin security group '$ADMIN_GROUP_NAME'..."
GROUP_JSON="$(az ad group list --display-name "$ADMIN_GROUP_NAME" --query '[0]' -o json)"
if [[ "$GROUP_JSON" == "null" || -z "$GROUP_JSON" ]]; then
  GROUP_JSON="$(az ad group create \
    --display-name "$ADMIN_GROUP_NAME" \
    --mail-nickname "$ADMIN_GROUP_NAME" \
    -o json)"
  echo "    created."
else
  echo "    already exists."
fi
GROUP_ID="$(echo "$GROUP_JSON" | jq -r '.id')"

# -----------------------------------------------------------------------------
# Summary block
# -----------------------------------------------------------------------------
cat <<EOF

==============================================================================
Entra provisioning complete for env='$ENV'.

Drop these into infra/parameters/${ENV}.bicepparam:

  param authMode = 'oidc'
  param entraTenantId      = '$TENANT_ID'
  param entraClientId      = '$API_APP_ID'
  param entraGroupIdAdmin  = '$GROUP_ID'

Frontend env (Static Web App app settings or .env.production):

  NEXT_PUBLIC_AUTH_MODE         = oidc
  NEXT_PUBLIC_ENTRA_TENANT_ID   = $TENANT_ID
  NEXT_PUBLIC_ENTRA_CLIENT_ID   = $SPA_APP_ID
  NEXT_PUBLIC_ENTRA_API_SCOPE   = $API_IDENTIFIER_URI/$SCOPE_NAME
  NEXT_PUBLIC_API_BASE_URL      = https://<api-hostname>

Next steps:
  1. Add operator/admin users to the security group:
       az ad group member add --group $GROUP_ID --member-id <user-object-id>
  2. Re-deploy the App Service with the bicepparam values above.
  3. Re-deploy the SWA with the frontend env vars above (LOCAL_DEV must NOT be
     set in cloud — backend will refuse to boot if AUTH_MODE=stub without it).
  4. [M5-5 notifier] If you intend to run the notifier worker with
     NOTIFIER_GRAPH_PROVIDER=azure (production), grant the notifier UAMI
     the Microsoft Graph application permission 'GroupMember.Read.All'
     and have a Global Administrator (or Privileged Role Administrator)
     grant tenant-wide admin consent:
       az ad app permission add --id <notifier-app-id> \\
           --api 00000003-0000-0000-c000-000000000000 \\
           --api-permissions 98830695-27a2-44f7-8c18-0c3ebc9698f6=Role
       az ad app permission admin-consent --id <notifier-app-id>
     Local dev uses NOTIFIER_GRAPH_PROVIDER=fake which returns a static
     admin list and needs no consent.
==============================================================================
EOF
