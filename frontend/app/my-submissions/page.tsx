"use client";

import { useEffect, useState } from "react";

import { ClassifierBadge, StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api/client";
import type { SkillListItem } from "@/lib/api/types";

export default function MySubmissionsPage() {
  const [rows, setRows] = useState<SkillListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const r = await api.me.submissions();
        if (!cancelled) {
          setRows(r);
          setLoaded(true);
        }
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    }
    void tick();
    const t = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  return (
    <div className="mx-auto max-w-[1280px] px-6 py-12">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="ms-eyebrow-blue">Contributor</span>
          <h1 className="mt-1 font-display text-[28px] font-bold tracking-ms-display text-ink">
            My submissions
          </h1>
          <p className="mt-1 text-sm text-muted">
            Auto-refreshing every 3 seconds.
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.15em] text-ms-green">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-pulse-dot rounded-full bg-ms-green opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-ms-green" />
          </span>
          Live
        </div>
      </header>

      {error && (
        <div className="mb-4 ms-msgbar-danger">
          <span>{error}</span>
        </div>
      )}

      <div className="ms-card overflow-hidden">
        <table className="ms-grid">
          <thead>
            <tr>
              <th>Skill</th>
              <th>Version</th>
              <th>Status</th>
              <th>Classifier</th>
              <th>Uploaded</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.skill_id}:${r.version}`}>
                <td>
                  <div className="font-display font-semibold text-ink">
                    {r.name}
                  </div>
                  <div className="font-mono text-xs text-muted">
                    {r.skill_id}
                  </div>
                </td>
                <td>
                  <span className="ms-chip font-mono">{r.version}</span>
                </td>
                <td>
                  <StatusBadge status={r.status} />
                </td>
                <td>
                  <ClassifierBadge status={r.classifier_status} />
                </td>
                <td className="text-xs text-muted">{r.uploaded_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {loaded && rows.length === 0 && (
          <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-bg-2 text-muted">
              <svg
                aria-hidden
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M4 6h16M4 12h16M4 18h10" />
              </svg>
            </div>
            <div className="font-display text-sm font-semibold text-ink">
              No submissions yet
            </div>
            <p className="max-w-xs text-xs text-muted">
              Once you upload a SKILL.md bundle it will show up here with live
              classifier status.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
