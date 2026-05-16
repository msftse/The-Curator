// MSAL configuration + lazy singleton client.
//
// Settings come from the runtime env (`window.__ENV__` injected by `/env.js`,
// see `frontend/lib/env.ts`). At build time we no longer know the tenant or
// client id — that's deliberate, so a single image promotes across envs.
// In stub mode (`AUTH_MODE !== "oidc"`) none of this is touched.
//
// We use `Redirect` flow (not popup) per spec: cleaner UX inside corporate
// browsers, fewer popup-blocker headaches, and `loginRedirect` round-trips
// land on `/auth/callback` which calls `handleRedirectPromise` and routes
// the user home.

import {
  PublicClientApplication,
  type Configuration,
  type SilentRequest,
  type RedirectRequest,
} from "@azure/msal-browser";

import {
  authMode,
  entraClientId,
  entraTenantId,
  entraApiScope,
  isOidc as isOidcEnv,
} from "@/lib/env";

/** Current auth mode, evaluated at call time (NOT module top). */
export function authModeValue(): "oidc" | "stub" {
  return authMode();
}

/** True when `AUTH_MODE === "oidc"`. Call at runtime — do NOT cache at module load. */
export function isOidc(): boolean {
  return isOidcEnv();
}

/**
 * Scope to request on the backend API. Should be exactly
 * `api://<api-app-id>/access_as_user`, emitted by `scripts/setup-entra.sh`.
 * Reads runtime env at call time.
 */
export function apiScope(): string {
  return entraApiScope();
}

/**
 * Redirect URI must match a configured SPA redirect on the app registration.
 * We always use `/auth/callback` on the current origin so prod + local dev
 * share the same code path.
 */
export function redirectUri(): string {
  if (typeof window === "undefined") return "";
  return `${window.location.origin}/auth/callback`;
}

function buildConfig(): Configuration {
  const tenantId = entraTenantId();
  const clientId = entraClientId();
  return {
    auth: {
      clientId,
      authority: tenantId
        ? `https://login.microsoftonline.com/${tenantId}`
        : "",
      redirectUri: redirectUri(),
      // Land on the home page after sign-out so we don't loop on /auth/callback.
      postLogoutRedirectUri:
        typeof window !== "undefined" ? window.location.origin : undefined,
      // Single-tenant — refuse tokens minted by other tenants.
      knownAuthorities: tenantId ? [`login.microsoftonline.com`] : [],
    },
    cache: {
      // sessionStorage keeps tokens scoped to the tab; localStorage would
      // share across tabs but is more exposure for very little gain on a
      // corp-internal SPA. Easy to flip later.
      cacheLocation: "sessionStorage",
      storeAuthStateInCookie: false,
    },
  };
}

// Lazy singleton — Next.js may render the layout on the server, where MSAL
// has nothing to do. The factory is called once, on the client, when the
// `AuthProvider` mounts AND runtime env has been injected.
let _client: PublicClientApplication | null = null;

export function getMsal(): PublicClientApplication {
  if (!isOidcEnv()) {
    throw new Error(
      "getMsal() called outside oidc mode — guard with isOidc() first.",
    );
  }
  const tenantId = entraTenantId();
  const clientId = entraClientId();
  if (!clientId || !tenantId) {
    throw new Error(
      "ENTRA_CLIENT_ID and ENTRA_TENANT_ID are required when AUTH_MODE=oidc. " +
        "Check that /env.js returned a populated window.__ENV__ payload.",
    );
  }
  if (_client === null) {
    _client = new PublicClientApplication(buildConfig());
  }
  return _client;
}

export function buildLoginRequest(): RedirectRequest {
  const scope = entraApiScope();
  return {
    scopes: scope ? [scope] : [],
    prompt: "select_account",
  };
}

export function buildSilentRequest(
  account: NonNullable<ReturnType<PublicClientApplication["getActiveAccount"]>>,
): SilentRequest {
  const scope = entraApiScope();
  return {
    scopes: scope ? [scope] : [],
    account,
  };
}
