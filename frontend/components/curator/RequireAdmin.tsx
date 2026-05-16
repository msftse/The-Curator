"use client";

import { useAdminProbe } from "@/lib/hooks/useAdminProbe";

export function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { isAdmin, isLoading, error } = useAdminProbe();

  if (isLoading) {
    return (
      <div className="space-y-3">
        <div className="h-6 w-32 animate-pulse rounded bg-gray-100" />
        <div className="h-24 animate-pulse rounded bg-gray-100" />
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="rounded border border-danger-border bg-danger-bg p-4 text-sm text-danger-fg">
        <h2 className="text-base font-semibold">Admins only.</h2>
        <p className="mt-1">
          You don&apos;t have access to the curator admin console.
        </p>
        <p className="mt-1">
          Your signed-in account is not a member of the{" "}
          <code>skillhub-admins</code> Entra security group. Ask a platform
          owner to add you, then sign out and back in to refresh your token.
        </p>
        {error ? (
          <p className="mt-2 text-xs text-danger-fg/80">
            (probe error: {String(error)})
          </p>
        ) : null}
      </div>
    );
  }

  return <>{children}</>;
}
