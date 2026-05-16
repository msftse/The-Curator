"use client";

// MSAL redirect landing page.
//
// `loginRedirect` round-trips users through Entra and lands them here with
// the auth response in the URL hash. The shared `AuthProvider` already
// processes `handleRedirectPromise` when it initialises (so the active
// account is set), so this page only needs to send the user home once
// MSAL has settled.

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { IS_OIDC, getMsal } from "@/lib/auth/msal";

export default function AuthCallbackPage() {
  const router = useRouter();

  useEffect(() => {
    if (!IS_OIDC) {
      router.replace("/");
      return;
    }
    let cancelled = false;
    const instance = getMsal();
    (async () => {
      try {
        await instance.initialize();
        // `AuthProvider` likely already drained this, but calling it again
        // is safe and returns `null` if there's nothing to process.
        await instance.handleRedirectPromise();
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("auth/callback: redirect processing failed", err);
      }
      if (!cancelled) router.replace("/");
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center text-sm text-muted">
      Completing sign-in…
    </div>
  );
}
