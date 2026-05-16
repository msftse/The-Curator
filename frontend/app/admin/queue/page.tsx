"use client";

import { useEffect, useState } from "react";

import { ClassifierBadge, StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api/client";
import type { SkillListItem } from "@/lib/api/types";

export default function ReviewQueuePage() {
  const [rows, setRows] = useState<SkillListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    try {
      const r = await api.admin.queue();
      setRows(r);
      setError(null);
    } catch (err) {
      setError(String(err));
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
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Review queue</h1>
      <p className="text-sm text-gray-600">
        Acting as <code>manager@org</code> required. Switch user in the top-right picker.
      </p>
      {error && (
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="py-2">Skill</th>
            <th>Uploader</th>
            <th>Status</th>
            <th>Classifier</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.skill_id}:${r.version}`} className="border-b">
              <td className="py-2">
                <div className="font-medium">{r.name}</div>
                <div className="text-xs text-gray-500">{r.skill_id}</div>
                {r.classification && (
                  <div className="mt-1 text-xs text-gray-600">
                    {r.classification.category} — {r.classification.summary}
                  </div>
                )}
              </td>
              <td className="text-xs">{r.uploader}</td>
              <td>
                <StatusBadge status={r.status} />
              </td>
              <td>
                <ClassifierBadge status={r.classifier_status} />
              </td>
              <td className="space-x-2">
                <button
                  disabled={busyId === r.skill_id}
                  onClick={() => approve(r.skill_id)}
                  className="rounded bg-emerald-600 px-3 py-1 text-white disabled:opacity-50"
                >
                  Approve
                </button>
                <button
                  disabled={busyId === r.skill_id}
                  onClick={() => reject(r.skill_id)}
                  className="rounded bg-rose-600 px-3 py-1 text-white disabled:opacity-50"
                >
                  Reject
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <p className="text-sm text-gray-600">No skills awaiting review.</p>
      )}
    </div>
  );
}
