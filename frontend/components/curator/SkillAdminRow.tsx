"use client";

import { useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { api } from "@/lib/api/client";
import type { SkillListItem } from "@/lib/api/types";

export function SkillAdminRow({
  skill,
  onMutated,
}: {
  skill: SkillListItem;
  onMutated: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    setBusy("pin");
    setError(null);
    try {
      if (skill.pinned) await api.curator.unpin(skill.skill_id);
      else await api.curator.pin(skill.skill_id);
      await onMutated();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  async function restore() {
    setBusy("restore");
    setError(null);
    try {
      await api.curator.restore(skill.skill_id);
      await onMutated();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <tr className="border-b">
      <td className="py-2">
        <div className="font-medium">{skill.name}</div>
        <div className="font-mono text-xs text-gray-500">{skill.skill_id}</div>
      </td>
      <td>
        <StatusBadge status={skill.status} />
      </td>
      <td>
        <button
          disabled={busy !== null}
          onClick={() => void toggle()}
          className={
            skill.pinned
              ? "rounded bg-indigo-600 px-3 py-1 text-xs text-white disabled:opacity-50"
              : "rounded border border-gray-300 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          }
        >
          {skill.pinned ? "Pinned · click to unpin" : "Pin"}
        </button>
      </td>
      <td>
        {skill.status === "archived" ? (
          <button
            disabled={busy !== null}
            onClick={() => void restore()}
            className="rounded bg-emerald-600 px-3 py-1 text-xs text-white disabled:opacity-50"
          >
            Restore
          </button>
        ) : (
          <span className="text-xs text-gray-400">—</span>
        )}
        {error ? (
          <div className="mt-1 text-xs text-rose-700">{error}</div>
        ) : null}
      </td>
    </tr>
  );
}
