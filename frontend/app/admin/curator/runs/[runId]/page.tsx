"use client";

import Link from "next/link";
import { useState } from "react";

import { RunRecordCard } from "@/components/curator/RunRecordCard";
import { MarkdownView } from "@/components/MarkdownView";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";
import type { CuratorRunRecord } from "@/lib/api/types";

export default function RunReportPage({
  params,
}: {
  params: { runId: string };
}) {
  const runId = decodeURIComponent(params.runId);
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
        <Link href="/admin/curator" className="text-sky-700 hover:underline">
          ← back to overview
        </Link>
      </div>
      <h2 className="text-lg font-semibold">
        Run <code className="font-mono">{runId}</code>
      </h2>

      <div className="flex gap-3 border-b border-gray-200 text-sm">
        {(["summary", "report"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={
              tab === t
                ? "border-b-2 border-sky-600 pb-1 font-semibold text-gray-900"
                : "pb-1 text-gray-600 hover:text-gray-900"
            }
          >
            {t === "summary" ? "Summary" : "Report"}
          </button>
        ))}
      </div>

      {tab === "summary" ? (
        runs.error ? (
          <div className="rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800">
            Recent-runs listing endpoint not available yet
            (<code>GET /v1/admin/curator/runs</code>).
            <div className="mt-1 text-amber-900/70">({String(runs.error)})</div>
          </div>
        ) : runs.isLoading ? (
          <div className="h-24 animate-pulse rounded bg-gray-100" />
        ) : rec ? (
          <RunRecordCard record={rec} />
        ) : (
          <p className="text-sm text-gray-500">Run not found in recent list.</p>
        )
      ) : report.error ? (
        <div className="rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-800">
          Run-report endpoint not available yet
          (<code>GET /v1/admin/curator/runs/&#123;run_id&#125;/report</code>).
          <div className="mt-1 text-amber-900/70">({String(report.error)})</div>
        </div>
      ) : report.isLoading ? (
        <div className="h-24 animate-pulse rounded bg-gray-100" />
      ) : (
        <MarkdownView source={report.data ?? ""} />
      )}
    </div>
  );
}
