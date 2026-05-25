"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api/client";
import { useAdminProbe } from "@/lib/hooks/useAdminProbe";
import type { SkillDetail } from "@/lib/api/types";

export function SkillDetailDefenderRescan({ skill }: { skill: SkillDetail }) {
  const { isAdmin, isLoading } = useAdminProbe();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (isLoading || !isAdmin || skill.status === "quarantined") return null;

  async function rescan() {
    setBusy(true);
    setError(null);
    try {
      await api.admin.defenderRescan(skill.skill_id);
      router.refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={() => void rescan()}
        disabled={busy}
        className="rounded border border-ink/20 px-3 py-1.5 text-xs font-medium text-ink-2 hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-50"
        title="Queue this skill for a new Defender scan"
      >
        {busy ? "Queued…" : "Rescan defender"}
      </button>
      {error && <div className="text-[11px] text-danger-fg">{error}</div>}
    </div>
  );
}
