"use client";

import { useEffect, useState } from "react";

import { ClassifierBadge, StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api/client";
import type { SkillListItem } from "@/lib/api/types";

export default function ReviewQueuePage() {
  const [rows, setRows] = useState<SkillListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    try {
      const r = await api.admin.queue();
      setRows(r);
      setError(null);
    } catch (err) {
      setError(String(err));
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function approve(id: string) {
    setBusyId(id);
    try {
      await api.admin.approve(id);
      await load();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusyId(null);
    }
  }

  async function reject(id: string) {
    const reason = window.prompt("Rejection reason:");
    if (!reason) return;
    setBusyId(id);
    try {
      await api.admin.reject(id, reason);
      await load();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="mx-auto max-w-[1280px] px-6 py-12">
      <header className="mb-6">
        <span className="ms-eyebrow-blue">Manager</span>
        <h1 className="mt-1 font-display text-[28px] font-bold tracking-ms-display text-ink">
          Review queue
        </h1>
        <p className="mt-1 text-sm text-muted">
          Acting as{" "}
          <code className="rounded bg-bg-2 px-1.5 py-0.5 font-mono text-[12px] text-ink-2">
            manager@org
          </code>{" "}
          is required. Switch personas in the top-right picker.
        </p>
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
              <th>Uploader</th>
              <th>Status</th>
              <th>Classifier</th>
              <th className="text-right">Actions</th>
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
                  {r.classification && (
                    <div className="mt-2 flex flex-wrap items-start gap-2 text-xs text-ink-2">
                      <span className="inline-flex shrink-0 rounded-full bg-ms-blue/10 px-2 py-0.5 font-semibold text-ms-blue">
                        {r.classification.category}
                      </span>
                      <span className="text-muted">
                        {r.classification.summary}
                      </span>
                    </div>
                  )}
                </td>
                <td className="text-xs text-ink-2">{r.uploader}</td>
                <td>
                  <StatusBadge status={r.status} />
                </td>
                <td>
                  <ClassifierBadge status={r.classifier_status} />
                </td>
                <td>
                  <div className="flex justify-end gap-2">
                    <button
                      disabled={busyId === r.skill_id}
                      onClick={() => approve(r.skill_id)}
                      className="ms-btn-success px-3 py-1 text-xs"
                    >
                      Approve
                    </button>
                    <button
                      disabled={busyId === r.skill_id}
                      onClick={() => reject(r.skill_id)}
                      className="ms-btn-danger px-3 py-1 text-xs"
                    >
                      Reject
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {loaded && rows.length === 0 && (
          <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-ms-green/10 text-ms-green">
              <svg
                aria-hidden
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M5 12l4 4 10-10" />
              </svg>
            </div>
            <div className="font-display text-sm font-semibold text-ink">
              Queue is empty
            </div>
            <p className="max-w-xs text-xs text-muted">
              All caught up — no skills are currently awaiting manager review.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
