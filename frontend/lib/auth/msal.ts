// MSAL configuration + lazy singleton client.
//
// All settings come from `NEXT_PUBLIC_ENTRA_*` env vars wired at build time
// by the SWA deploy workflow (see `scripts/setup-entra.sh` output block).
// In stub mode (`NEXT_PUBLIC_AUTH_MODE !== "oidc"`) none of this is touched.
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

export const AUTH_MODE =
  (process.env.NEXT_PUBLIC_AUTH_MODE as "oidc" | "stub" | undefined) ?? "stub";

export const IS_OIDC = AUTH_MODE === "oidc";

const TENANT_ID = process.env.NEXT_PUBLIC_ENTRA_TENANT_ID ?? "";
const CLIENT_ID = process.env.NEXT_PUBLIC_ENTRA_CLIENT_ID ?? "";

/**
 * Scope to request on the backend API. Should be exactly
 * `api://skillhub-<env>/access_as_user`, emitted by `scripts/setup-entra.sh`.
 */
export const API_SCOPE = process.env.NEXT_PUBLIC_ENTRA_API_SCOPE ?? "";

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
  return {
    auth: {
      clientId: CLIENT_ID,
      authority: TENANT_ID
        ? `https://login.microsoftonline.com/${TENANT_ID}`
        : "",
      redirectUri: redirectUri(),
      // Land on the home page after sign-out so we don't loop on /auth/callback.
      postLogoutRedirectUri:
        typeof window !== "undefined" ? window.location.origin : undefined,
      // Single-tenant — refuse tokens minted by other tenants.
      knownAuthorities: TENANT_ID
        ? [`login.microsoftonline.com`]
        : [],
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
// `AuthProvider` mounts.
let _client: PublicClientApplication | null = null;

export function getMsal(): PublicClientApplication {
  if (!IS_OIDC) {
    throw new Error(
      "getMsal() called outside oidc mode — guard with IS_OIDC first.",
    );
  }
  if (!CLIENT_ID || !TENANT_ID) {
    throw new Error(
      "NEXT_PUBLIC_ENTRA_CLIENT_ID and NEXT_PUBLIC_ENTRA_TENANT_ID are required when AUTH_MODE=oidc.",
    );
  }
  if (_client === null) {
    _client = new PublicClientApplication(buildConfig());
  }
  return _client;
}

export function buildLoginRequest(): RedirectRequest {
  return {
    scopes: API_SCOPE ? [API_SCOPE] : [],
    prompt: "select_account",
  };
}

export function buildSilentRequest(account: NonNullable<
  ReturnType<PublicClientApplication["getActiveAccount"]>
>): SilentRequest {
  return {
    scopes: API_SCOPE ? [API_SCOPE] : [],
    account,
  };
}
