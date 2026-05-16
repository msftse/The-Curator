"use client";

import { SnapshotRow } from "@/components/curator/SnapshotRow";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";

export default function SnapshotsPage() {
  const { data, error, isLoading, mutate } = useResource(
    ["curator", "snapshots"],
    () => api.curator.listSnapshots(),
  );

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Snapshots</h2>
      <p className="text-sm text-gray-600">
        Each snapshot is a point-in-time copy of the published catalog.
        Rollback restores the catalog to the selected snapshot; a pre-rollback
        snapshot is captured first.
      </p>

      {error ? (
        <div className="ms-msgbar-warning">
          Failed to load snapshots.
          <div className="mt-1 text-xs text-warning-fg/80">({String(error)})</div>
        </div>
      ) : isLoading ? (
        <div className="h-16 animate-pulse rounded bg-gray-100" />
      ) : (data ?? []).length === 0 ? (
        <p className="text-sm text-gray-500">No snapshots yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-gray-500">
              <th className="py-2">Name</th>
              <th>Captured</th>
              <th>Skills</th>
              <th>Size</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((snap) => (
              <SnapshotRow
                key={snap.name}
                snap={snap}
                onRolledBack={() => void mutate()}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
