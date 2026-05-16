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
            ? "border-danger-border bg-danger-bg"
            : "border-success-border bg-success-bg"
        }`}
      >
        <div className="text-xs uppercase text-muted">Pause state</div>
        <div className="mt-1 text-lg font-semibold text-ink">
          {status.paused ? "Paused" : "Running"}
        </div>
      </div>

      <div
        className={`rounded border p-3 ${
          status.lock_held
            ? "border-warning-border bg-warning-bg"
            : "border-line bg-white"
        }`}
      >
        <div className="text-xs uppercase text-muted">Run lock</div>
        <div className="mt-1 text-lg font-semibold text-ink">
          {status.lock_held ? "Held" : "Free"}
        </div>
      </div>

      <div className="rounded border border-line bg-white p-3">
        <div className="text-xs uppercase text-muted">Last run</div>
        {lastRun ? (
          <Link
            href={`/admin/curator/runs/${encodeURIComponent(lastRun.run_id)}`}
            className="mt-1 block text-sm font-medium text-ms-blue hover:underline"
          >
            {lastRun.run_id}
          </Link>
        ) : (
          <div className="mt-1 text-sm text-muted">No runs yet</div>
        )}
        <div className="text-xs text-muted">
          {lastRun ? formatDate(lastRun.finished_at) : ""}
          {lastRun?.dry_run ? " (dry-run)" : ""}
        </div>
      </div>

      <div className="rounded border border-line bg-white p-3">
        <div className="text-xs uppercase text-muted">Last snapshot</div>
        {lastRun?.snapshot_name ? (
          <Link
            href="/admin/curator/snapshots"
            className="mt-1 block text-sm font-medium text-ms-blue hover:underline"
          >
            {lastRun.snapshot_name}
          </Link>
        ) : (
          <div className="mt-1 text-sm text-muted">None</div>
        )}
      </div>
    </div>
  );
}
