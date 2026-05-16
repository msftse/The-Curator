"use client";

import Link from "next/link";
import { use, useState } from "react";

import { RunRecordCard } from "@/components/curator/RunRecordCard";
import { MarkdownView } from "@/components/MarkdownView";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";
import type { CuratorRunRecord } from "@/lib/api/types";

export default function RunReportPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId: rawRunId } = use(params);
  const runId = decodeURIComponent(rawRunId);
  const [tab, setTab] = useState<"summary" | "report">("summary");

  const runs = useResource(["curator", "runs", 50], () =>
    api.curator.listRuns({ limit: 50 }),
  );
  const report = useResource(["curator", "run-report", runId], () =>
    api.curator.getRunReport(runId),
  );

  const rec: CuratorRunRecord | undefined = (runs.data ?? []).find(
    (r) => r.run_id === runId,
  );

  return (
    <div className="space-y-4">
      <div className="text-xs">
        <Link href="/admin/curator" className="text-ms-blue hover:underline">
          ← back to overview
        </Link>
      </div>
      <h2 className="text-lg font-semibold text-ink">
        Run <code className="font-mono">{runId}</code>
      </h2>

      <div className="flex gap-3 border-b border-line text-sm">
        {(["summary", "report"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={
              tab === t
                ? "border-b-2 border-ms-blue pb-1 font-semibold text-ink"
                : "pb-1 text-muted hover:text-ink"
            }
          >
            {t === "summary" ? "Summary" : "Report"}
          </button>
        ))}
      </div>

      {tab === "summary" ? (
        runs.error ? (
          <div className="ms-msgbar-warning text-xs">
            Recent-runs listing endpoint not available yet
            (<code>GET /v1/admin/curator/runs</code>).
            <div className="mt-1 text-warning-fg/80">({String(runs.error)})</div>
          </div>
        ) : runs.isLoading ? (
          <div className="h-24 animate-pulse rounded bg-bg-2" />
        ) : rec ? (
          <RunRecordCard record={rec} />
        ) : (
          <p className="text-sm text-muted">Run not found in recent list.</p>
        )
      ) : report.error ? (
        <div className="ms-msgbar-warning text-xs">
          Failed to load run report.
          <div className="mt-1 text-warning-fg/80">({String(report.error)})</div>
        </div>
      ) : report.isLoading ? (
        <div className="h-24 animate-pulse rounded bg-bg-2" />
      ) : (
        <MarkdownView source={report.data ?? ""} />
      )}
    </div>
  );
}
