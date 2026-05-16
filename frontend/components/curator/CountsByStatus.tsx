"use client";

import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";

/**
 * Best-effort counts. The public catalog list only returns approved skills,
 * so counts for `stale` / `archived` / `pinned` will read 0 here until a
 * backend admin-scoped listing endpoint exists. The header makes that limit
 * explicit instead of silently lying.
 */
export function CountsByStatus() {
  const { data, error, isLoading } = useResource(["catalog", "list"], () =>
    api.catalog.list(),
  );

  if (isLoading) {
    return <div className="h-16 animate-pulse rounded bg-gray-100" />;
  }
  if (error) {
    return (
      <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
        Counts unavailable: {String(error)}
      </div>
    );
  }

  const rows = data ?? [];
  const counts: Record<string, number> = {
    approved: 0,
    stale: 0,
    archived: 0,
    pinned: 0,
  };
  for (const r of rows) {
    if (r.status in counts) counts[r.status] += 1;
    if (r.pinned) counts.pinned += 1;
  }

  return (
    <div>
      <div className="text-xs uppercase text-gray-500">
        Counts (from public catalog)
      </div>
      <div className="mt-1 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(["approved", "stale", "archived", "pinned"] as const).map((k) => (
          <div
            key={k}
            className="rounded border border-gray-200 bg-white p-3 text-center"
          >
            <div className="text-2xl font-semibold">{counts[k]}</div>
            <div className="text-xs uppercase text-gray-500">{k}</div>
          </div>
        ))}
      </div>
      <p className="mt-1 text-xs text-gray-500">
        Note: stale / archived counts require an admin-scoped listing endpoint
        (not yet implemented).
      </p>
    </div>
  );
}
