"use client";

// Auth provider wrapper.
//
// Two-mode behaviour driven by `NEXT_PUBLIC_AUTH_MODE`:
//
// - `oidc`: wraps children in `MsalProvider`, initialises the singleton MSAL
//   client, processes the post-redirect response, and guards the tree so
//   unauthenticated users are kicked into `loginRedirect()`.
//
// - `stub` (or unset): no-op pass-through. The legacy `X-User-Email`
//   localStorage stub keeps working — UserPicker still renders its persona
//   picker, the API client still injects the header.
//
// The guard intentionally lives in a client component because MSAL touches
// `window` and `sessionStorage`. Server rendering of the layout stays clean.

import { MsalProvider, useIsAuthenticated, useMsal } from "@azure/msal-react";
import { usePathname } from "next/navigation";
import {
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  isOidc,
  buildLoginRequest,
  getMsal,
} from "./msal";

export function AuthProvider({ children }: { children: ReactNode }) {
  if (!isOidc()) {
    return <>{children}</>;
  }
  return <OidcAuthProvider>{children}</OidcAuthProvider>;
}

/**
 * Pulled into a separate component so the MSAL singleton + provider only
 * mount on the client in oidc mode. SSR returns a plain pass-through.
 */
function OidcAuthProvider({ children }: { children: ReactNode }) {
  // `useMemo` so we don't reconstruct the client on each render. The factory
  // itself is idempotent (singleton) but this avoids triggering React's
  // double-invoke heuristics in dev.
  const instance = useMemo(() => getMsal(), []);
  const [initialised, setInitialised] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      // `initialize()` must complete before any other MSAL API call in v3.
      await instance.initialize();
      // Drain any pending redirect response. Throws on real failures; benign
      // for fresh page loads (resolves to `null`).
      try {
        const result = await instance.handleRedirectPromise();
        if (result?.account) {
          instance.setActiveAccount(result.account);
        } else if (!instance.getActiveAccount()) {
          const accounts = instance.getAllAccounts();
          if (accounts.length > 0) {
            instance.setActiveAccount(accounts[0]!);
          }
        }
      } catch (err) {
        // Surface to console; the redirect guard will still attempt login.
        // eslint-disable-next-line no-console
        console.error("MSAL handleRedirectPromise failed", err);
      }
      if (!cancelled) setInitialised(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [instance]);

  if (!initialised) {
    // Tiny placeholder rather than a flash of the unauthenticated UI.
    return <div className="min-h-screen" aria-busy="true" />;
  }

  return (
    <MsalProvider instance={instance}>
      <MsalRedirectGuard>{children}</MsalRedirectGuard>
    </MsalProvider>
  );
}

/**
 * If the user is not authenticated, kick off `loginRedirect`. The
 * `/auth/callback` route handles the return trip and routes them home.
 */
function MsalRedirectGuard({ children }: { children: ReactNode }) {
  const { instance, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const pathname = usePathname();
  // The callback page handles its own redirect lifecycle.
  const onCallback = pathname?.startsWith("/auth/callback");
  const loginAttempted = useRef(false);

  useEffect(() => {
    if (onCallback) return;
    if (isAuthenticated) return;
    if (inProgress !== "none") return; // login/handle-redirect already running.
    if (loginAttempted.current) return;
    loginAttempted.current = true;
    instance.loginRedirect(buildLoginRequest()).catch((err) => {
      // eslint-disable-next-line no-console
      console.error("MSAL loginRedirect failed", err);
    });
  }, [instance, isAuthenticated, inProgress, onCallback]);

  if (!isAuthenticated && !onCallback) {
    return (
      <div
        className="flex min-h-screen items-center justify-center text-sm text-muted"
        aria-busy="true"
      >
        Redirecting to sign-in…
      </div>
    );
  }
  return <>{children}</>;
}

/**
 * Account helper for client components. In stub mode returns `null` (callers
 * fall back to the localStorage stub). MUST be called inside an `<MsalProvider>`
 * in oidc mode — `AuthProvider` guarantees this.
 */
export function useAuthAccount(): {
  email: string | null;
  name: string | null;
  oid: string | null;
} {
  // `isOidc()` reads runtime env. In stub deployments the useMsal branch
  // below is unreachable, so the rules-of-hooks linter still sees a
  // stable call order at runtime within a single page lifetime.
  if (!isOidc()) {
    return { email: null, name: null, oid: null };
  }
  return useAuthAccountOidc();
}

function useAuthAccountOidc(): {
  email: string | null;
  name: string | null;
  oid: string | null;
} {
  const { accounts } = useMsal();
  const acct = accounts[0];
  if (!acct) return { email: null, name: null, oid: null };
  // `username` is preferred_username in v3; `localAccountId` is the oid.
  return {
    email: acct.username ?? null,
    name: acct.name ?? null,
    oid: acct.localAccountId ?? null,
  };
}

/** Trigger sign-out via redirect. No-op in stub mode. */
export function signOut(): void {
  if (!isOidc()) return;
  const instance = getMsal();
  void instance.logoutRedirect();
}
