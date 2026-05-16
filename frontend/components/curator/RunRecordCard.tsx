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
  steady_state: "bg-gray-100 text-gray-700",
  stale_30d: "bg-amber-100 text-amber-800",
  archive_90d: "bg-rose-100 text-rose-800",
  pinned: "bg-indigo-100 text-indigo-800",
  missing_usage_data: "bg-gray-200 text-gray-700",
};

export function RunRecordCard({ record }: { record: CuratorRunRecord }) {
  return (
    <div className="rounded border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        <code className="text-sm font-semibold">{record.run_id}</code>
        {record.dry_run ? (
          <span className="inline-block rounded bg-sky-100 px-2 py-0.5 text-xs text-sky-800">
            dry-run
          </span>
        ) : (
          <span className="inline-block rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">
            applied
          </span>
        )}
        <span className="text-xs text-gray-500">
          {formatDate(record.started_at)} → {formatDate(record.finished_at)}
        </span>
      </div>

      {record.snapshot_name ? (
        <div className="mt-2 text-xs">
          Snapshot:{" "}
          <Link
            href="/admin/curator/snapshots"
            className="font-mono text-sky-700 hover:underline"
          >
            {record.snapshot_name}
          </Link>
        </div>
      ) : null}

      <div className="mt-3">
        <div className="text-xs uppercase text-gray-500">
          Transitions ({record.transitions.length})
        </div>
        {record.transitions.length === 0 ? (
          <div className="mt-1 text-sm text-gray-500">
            No transitions — steady state.
          </div>
        ) : (
          <table className="mt-1 w-full text-sm">
            <thead>
              <tr className="border-b text-left text-gray-500">
                <th className="py-1">Skill</th>
                <th>Before</th>
                <th>After</th>
                <th>Reason</th>
                <th>Applied</th>
              </tr>
            </thead>
            <tbody>
              {record.transitions.map((t, i) => (
                <tr key={`${t.skill_id}:${i}`} className="border-b">
                  <td className="py-1 font-mono text-xs">{t.skill_id}</td>
                  <td>{t.before}</td>
                  <td>{t.after}</td>
                  <td>
                    <span
                      className={`inline-block rounded px-2 py-0.5 text-xs ${
                        REASON_COLORS[t.reason] ?? "bg-gray-100 text-gray-700"
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
          <div className="text-xs uppercase text-gray-500">Skipped (pinned)</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {record.skipped_pinned.map((s) => (
              <span
                key={s}
                className="inline-block rounded bg-indigo-100 px-2 py-0.5 font-mono text-xs text-indigo-800"
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
