"use client";

import Link from "next/link";
import { useState } from "react";

import { CountsByStatus } from "@/components/curator/CountsByStatus";
import { JanitorPanel } from "@/components/curator/JanitorPanel";
import { RunControls } from "@/components/curator/RunControls";
import { RunRecordCard } from "@/components/curator/RunRecordCard";
import { StatusCards } from "@/components/curator/StatusCards";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";
import type { CuratorRunRecord } from "@/lib/api/types";

export default function CuratorDashboardPage() {
  const status = useResource(["curator", "status"], () =>
    api.curator.status(),
  );
  const runs = useResource(["curator", "runs", 10], () =>
    api.curator.listRuns({ limit: 10 }),
  );

  const [lastDryRun, setLastDryRun] = useState<CuratorRunRecord | null>(null);

  return (
    <div className="space-y-6">
      {status.error ? (
        <div className="ms-msgbar-danger">
          Status unavailable: {String(status.error)}
        </div>
      ) : null}

      {status.data ? <StatusCards status={status.data} /> : null}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold uppercase text-gray-600">
          Controls
        </h2>
        <RunControls
          status={status.data}
          onMutated={async () => {
            await status.mutate();
            await runs.mutate();
          }}
          onDryRun={(r) => setLastDryRun(r)}
          onRun={(r) => setLastDryRun(r)}
        />
      </section>

      <section>
        <CountsByStatus />
      </section>

      <section className="space-y-2">
        <h2 className="text-sm font-semibold uppercase text-gray-600">
          Maintenance
        </h2>
        <JanitorPanel onMutated={() => status.mutate()} />
      </section>

      {lastDryRun ? (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold uppercase text-gray-600">
            Last (dry-)run output
          </h2>
          <RunRecordCard record={lastDryRun} />
        </section>
      ) : null}

      <section className="space-y-2">
        <h2 className="text-sm font-semibold uppercase text-gray-600">
          Recent runs
        </h2>
        {runs.error ? (
          <div className="ms-msgbar-warning text-xs">
            Failed to load recent runs.
            <div className="mt-1 text-warning-fg/80">({String(runs.error)})</div>
          </div>
        ) : runs.isLoading ? (
          <div className="h-12 animate-pulse rounded bg-gray-100" />
        ) : (runs.data ?? []).length === 0 ? (
          <p className="text-sm text-gray-500">No prior runs.</p>
        ) : (
          <ul className="divide-y rounded border border-gray-200 bg-white">
            {(runs.data ?? []).map((r) => (
              <li key={r.run_id} className="px-3 py-2 text-sm">
                <Link
                  href={`/admin/curator/runs/${encodeURIComponent(r.run_id)}`}
                  className="font-mono text-ms-blue hover:underline"
                >
                  {r.run_id}
                </Link>
                <span className="ml-2 text-xs text-gray-500">
                  {r.dry_run ? "dry-run · " : ""}
                  {r.transitions.length} transitions
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-600">
          Last report preview
        </h2>
        {status.data?.last_run ? (
          <div className="mt-2 rounded border border-gray-200 bg-white p-3">
            <RunRecordCard record={status.data.last_run} />
          </div>
        ) : (
          <p className="mt-2 text-sm text-gray-500">
            No last run recorded in status.
          </p>
        )}
      </section>

      {/* Reports for individual runs are at /admin/curator/runs/[runId]. */}
    </div>
  );
}
