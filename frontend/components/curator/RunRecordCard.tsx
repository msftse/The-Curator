import Link from "next/link";

import type { CuratorRunRecord } from "@/lib/api/types";

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const REASON_COLORS: Record<string, string> = {
  steady_state: "bg-bg-2 text-ink-2",
  stale_30d: "bg-warning-bg text-warning-fg",
  archive_90d: "bg-danger-bg text-danger-fg",
  pinned: "bg-violet-dim text-violet-dark",
  missing_usage_data: "bg-bg-2 text-muted-2",
};

export function RunRecordCard({ record }: { record: CuratorRunRecord }) {
  return (
    <div className="rounded border border-line bg-white p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <code className="text-sm font-semibold text-ink">{record.run_id}</code>
        {record.dry_run ? (
          <span className="inline-block rounded bg-info-bg px-2 py-0.5 text-xs text-info-fg">
            dry-run
          </span>
        ) : (
          <span className="inline-block rounded bg-success-bg px-2 py-0.5 text-xs text-success-fg">
            applied
          </span>
        )}
        <span className="text-xs text-muted">
          {formatDate(record.started_at)} → {formatDate(record.finished_at)}
        </span>
      </div>

      {record.snapshot_name ? (
        <div className="mt-2 text-xs">
          Snapshot:{" "}
          <Link
            href="/admin/curator/snapshots"
            className="font-mono text-ms-blue hover:underline"
          >
            {record.snapshot_name}
          </Link>
        </div>
      ) : null}

      <div className="mt-3">
        <div className="text-xs uppercase text-muted">
          Transitions ({record.transitions.length})
        </div>
        {record.transitions.length === 0 ? (
          <div className="mt-1 text-sm text-muted">
            No transitions — steady state.
          </div>
        ) : (
          <table className="mt-1 w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-muted">
                <th className="py-1">Skill</th>
                <th>Before</th>
                <th>After</th>
                <th>Reason</th>
                <th>Applied</th>
              </tr>
            </thead>
            <tbody>
              {record.transitions.map((t, i) => (
                <tr key={`${t.skill_id}:${i}`} className="border-b border-line">
                  <td className="py-1 font-mono text-xs">{t.skill_id}</td>
                  <td>{t.before}</td>
                  <td>{t.after}</td>
                  <td>
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-xs ${
                        REASON_COLORS[t.reason] ?? "bg-bg-2 text-ink-2"
                      }`}
                    >
                      {t.reason}
                    </span>
                  </td>
                  <td>{t.applied ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {record.skipped_pinned.length > 0 ? (
        <div className="mt-3">
          <div className="text-xs uppercase text-muted">Skipped (pinned)</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {record.skipped_pinned.map((s) => (
              <span
                key={s}
                className="inline-block rounded bg-violet-dim px-2 py-0.5 font-mono text-xs text-violet-dark"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
