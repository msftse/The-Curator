"use client";

import { useMemo, useState } from "react";

import { SkillAdminRow } from "@/components/curator/SkillAdminRow";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";
import type { SkillStatus } from "@/lib/api/types";

const STATUSES: SkillStatus[] = [
  "pending",
  "classified",
  "approved",
  "rejected",
  "stale",
  "archived",
];

export default function SkillsAdminPage() {
  const { data, error, isLoading, mutate } = useResource(
    ["catalog", "list"],
    () => api.catalog.list(),
  );

  const [filter, setFilter] = useState<Set<SkillStatus>>(
    new Set(["approved", "stale", "archived"]),
  );
  const [pinnedOnly, setPinnedOnly] = useState(false);

  const rows = useMemo(() => {
    const all = data ?? [];
    return all.filter((s) => {
      if (pinnedOnly && !s.pinned) return false;
      return filter.has(s.status);
    });
  }, [data, filter, pinnedOnly]);

  function toggle(s: SkillStatus) {
    const next = new Set(filter);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    setFilter(next);
  }

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Skills (admin)</h2>
      <p className="text-sm text-gray-600">
        Per-skill admin actions: pin / unpin, and restore for archived skills.
        Note: the public catalog API only returns <code>approved</code> skills
        today, so archived/stale rows won&apos;t appear until an admin-scoped
        listing endpoint exists.
      </p>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-gray-600">Status:</span>
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => toggle(s)}
            className={
              filter.has(s)
                ? "rounded bg-gray-900 px-2 py-1 text-white"
                : "rounded border border-gray-300 px-2 py-1 text-gray-700 hover:bg-gray-50"
            }
          >
            {s}
          </button>
        ))}
        <label className="ml-2 inline-flex items-center gap-1">
          <input
            type="checkbox"
            checked={pinnedOnly}
            onChange={(e) => setPinnedOnly(e.target.checked)}
          />
          pinned only
        </label>
      </div>

      {error ? (
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          {String(error)}
        </div>
      ) : isLoading ? (
        <div className="h-16 animate-pulse rounded bg-gray-100" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-gray-500">No skills match the filter.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-gray-500">
              <th className="py-2">Skill</th>
              <th>Status</th>
              <th>Pin</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <SkillAdminRow
                key={`${s.skill_id}:${s.version}`}
                skill={s}
                onMutated={() => mutate()}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
