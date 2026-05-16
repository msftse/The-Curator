import Link from "next/link";

import type { CuratorStatus } from "@/lib/api/types";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function StatusCards({ status }: { status: CuratorStatus }) {
  const lastRun = status.last_run;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <div
        className={`rounded border p-3 ${
          status.paused
            ? "border-rose-300 bg-rose-50"
            : "border-emerald-200 bg-emerald-50"
        }`}
      >
        <div className="text-xs uppercase text-gray-500">Pause state</div>
        <div className="mt-1 text-lg font-semibold">
          {status.paused ? "Paused" : "Running"}
        </div>
      </div>

      <div
        className={`rounded border p-3 ${
          status.lock_held
            ? "border-amber-300 bg-amber-50"
            : "border-gray-200 bg-white"
        }`}
      >
        <div className="text-xs uppercase text-gray-500">Run lock</div>
        <div className="mt-1 text-lg font-semibold">
          {status.lock_held ? "Held" : "Free"}
        </div>
      </div>

      <div className="rounded border border-gray-200 bg-white p-3">
        <div className="text-xs uppercase text-gray-500">Last run</div>
        {lastRun ? (
          <Link
            href={`/admin/curator/runs/${encodeURIComponent(lastRun.run_id)}`}
            className="mt-1 block text-sm font-medium text-sky-700 hover:underline"
          >
            {lastRun.run_id}
          </Link>
        ) : (
          <div className="mt-1 text-sm text-gray-500">No runs yet</div>
        )}
        <div className="text-xs text-gray-500">
          {lastRun ? formatDate(lastRun.finished_at) : ""}
          {lastRun?.dry_run ? " (dry-run)" : ""}
        </div>
      </div>

      <div className="rounded border border-gray-200 bg-white p-3">
        <div className="text-xs uppercase text-gray-500">Last snapshot</div>
        {lastRun?.snapshot_name ? (
          <Link
            href="/admin/curator/snapshots"
            className="mt-1 block text-sm font-medium text-sky-700 hover:underline"
          >
            {lastRun.snapshot_name}
          </Link>
        ) : (
          <div className="mt-1 text-sm text-gray-500">None</div>
        )}
      </div>
    </div>
  );
}
