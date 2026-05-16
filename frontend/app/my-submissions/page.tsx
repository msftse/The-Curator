"use client";

import { useEffect, useState } from "react";

import { ClassifierBadge, StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api/client";
import type { SkillListItem } from "@/lib/api/types";

export default function MySubmissionsPage() {
  const [rows, setRows] = useState<SkillListItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const r = await api.me.submissions();
        if (!cancelled) setRows(r);
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    }
    tick();
    const t = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">My submissions</h1>
      {error && (
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          {error}
        </div>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="py-2">Skill</th>
            <th>Version</th>
            <th>Status</th>
            <th>Classifier</th>
            <th>Uploaded</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.skill_id}:${r.version}`} className="border-b">
              <td className="py-2">
                <div className="font-medium">{r.name}</div>
                <div className="text-xs text-gray-500">{r.skill_id}</div>
              </td>
              <td>{r.version}</td>
              <td>
                <StatusBadge status={r.status} />
              </td>
              <td>
                <ClassifierBadge status={r.classifier_status} />
              </td>
              <td className="text-xs text-gray-600">{r.uploaded_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && (
        <p className="text-sm text-gray-600">No submissions yet.</p>
      )}
    </div>
  );
}
