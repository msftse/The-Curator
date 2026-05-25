"use client";

import type { DefenderReport, DefenderSeverity, DefenderStatus } from "@/lib/api/types";

/**
 * Renders a DefenderReport (M5-2) on the catalog detail page.
 *
 * Visible to all readers, not just admins — the report is an audit
 * signal, not a secret. Admin-only controls (override / quarantine
 * buttons) live in a sibling component (`SkillDetailAdminActions`).
 *
 * Layout decisions:
 *  - Severity badge + scan timestamp at the top, big and easy to scan.
 *  - Findings as a vertical list. Each finding shows
 *    rule · severity · location with the excerpt and explanation
 *    inline. We deliberately do NOT collapse findings — the whole
 *    point of the panel is to make them readable in one glance.
 *  - "pending" / "scanning" / "failed" statuses render a one-line
 *    note instead of an empty card.
 */

const SEVERITY_STYLES: Record<DefenderSeverity, string> = {
  clean: "bg-ms-green/15 text-ms-green",
  low: "bg-amber-100 text-amber-800",
  medium: "bg-orange-100 text-orange-800",
  high: "bg-ms-red/15 text-ms-red",
  critical: "bg-ms-red text-white",
};

function SeverityBadge({ severity }: { severity: DefenderSeverity | null | undefined }) {
  const sev = severity ?? "clean";
  const cls = SEVERITY_STYLES[sev as DefenderSeverity] ?? SEVERITY_STYLES.clean;
  return (
    <span
      data-testid="defender-severity-badge"
      className={`ms-chip text-[11px] font-semibold uppercase tracking-wide ${cls}`}
    >
      {sev}
    </span>
  );
}

function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

export interface DefenderReportPanelProps {
  status: DefenderStatus | undefined;
  skillStatus?: string;
  severity?: DefenderSeverity | string | null;
  report?: DefenderReport | null;
  scannedAt?: string | null;
  compact?: boolean;
}

/**
 * Test-friendly entry point. `status` is required (it's the state
 * machine's source of truth); everything else is optional because the
 * report only exists post-scan.
 */
export function DefenderReportPanel({
  status,
  skillStatus,
  severity,
  report,
  scannedAt,
  compact = false,
}: DefenderReportPanelProps) {
  // No defender activity at all — render nothing rather than a stale
  // "pending" placeholder on legacy pre-M5 docs.
  if (!status) return null;

  if (status === "pending" || status === "scanning") {
    return (
      <aside
        className={`${compact ? "rounded border border-line bg-bg p-3" : "ms-card p-5"} flex flex-col gap-2`}
        data-testid="defender-panel"
        data-status={status}
      >
        <h2 className="font-display text-[11px] font-bold uppercase tracking-[0.18em] text-ink-2">
          Defender
        </h2>
        <p className={`${compact ? "text-xs" : "text-sm"} text-muted`}>
          Security scan {status === "pending" ? "queued" : "in progress"}.{" "}
          {skillStatus === "approved"
            ? "This skill is already published; the scan status is shown for audit visibility."
            : "The skill cannot be approved until the scan completes."}
        </p>
      </aside>
    );
  }

  if (status === "failed") {
    return (
      <aside
        className={`${compact ? "rounded border border-ms-red/30 bg-bg p-3" : "ms-card p-5"} flex flex-col gap-2 border-l-4 border-ms-red`}
        data-testid="defender-panel"
        data-status={status}
      >
        <h2 className="font-display text-[11px] font-bold uppercase tracking-[0.18em] text-ms-red">
          Defender — scan failed
        </h2>
        <p className={`${compact ? "text-xs" : "text-sm"} text-muted`}>
          The defender worker could not complete the scan. The janitor will
          re-queue this skill automatically; no action required.
        </p>
        {report?.notes && (
          <p className="text-xs text-muted">Note: {report.notes}</p>
        )}
      </aside>
    );
  }

  // status is "clean" or "flagged"
  const flagged = status === "flagged";
  const sev = (severity ?? report?.overall_severity ?? "clean") as DefenderSeverity;
  const findings = report?.findings ?? [];

  return (
    <aside
      className={`${compact ? "rounded border border-line bg-bg p-3" : "ms-card p-5"} flex flex-col gap-4 ${
        flagged ? "border-l-4 border-ms-red" : ""
      }`}
      data-testid="defender-panel"
      data-status={status}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="font-display text-[11px] font-bold uppercase tracking-[0.18em] text-ink-2">
            Defender
          </h2>
          <div className="flex items-center gap-2">
            <SeverityBadge severity={sev} />
            {flagged && (
              <span className="text-xs font-semibold text-ms-red">
                Flagged — admin action required
              </span>
            )}
            {!flagged && (
              <span className="text-xs text-muted">No findings.</span>
            )}
          </div>
        </div>
        <div className="text-right text-[11px] text-muted">
          <div>Scanned</div>
          <div>{formatTimestamp(scannedAt ?? report?.scanned_at)}</div>
          {report?.model && (
            <div className="mt-1 font-mono text-[10px]">{report.model}</div>
          )}
        </div>
      </header>

      {findings.length > 0 && (
        <ul
          className="flex flex-col gap-3"
          data-testid="defender-findings"
        >
          {findings.map((f, i) => (
            <li
              key={`${f.rule}:${f.location}:${i}`}
              className="rounded border border-line/60 bg-bg-2 p-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <code className="font-mono text-[12px] font-semibold text-ink">
                  {f.rule}
                </code>
                <SeverityBadge severity={f.severity as DefenderSeverity} />
              </div>
              <div className="mt-1 font-mono text-[11px] text-muted">
                {f.location}
              </div>
              {f.excerpt && (
                <pre className="mt-2 overflow-x-auto rounded bg-white p-2 font-mono text-[11px] text-ink-2">
                  {f.excerpt}
                </pre>
              )}
              {f.explanation && (
                <p className="mt-2 text-xs text-ink-2">{f.explanation}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
