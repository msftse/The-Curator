"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api/client";
import { useAdminProbe } from "@/lib/hooks/useAdminProbe";
import type { SkillDetail } from "@/lib/api/types";

export function SkillDetailClassifierActions({ skill }: { skill: SkillDetail }) {
  const { isAdmin, isLoading } = useAdminProbe();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const needsClassification = skill.classifier_status !== "done" || skill.classification === null;
  if (isLoading || !isAdmin || !needsClassification) return null;

  async function classifyNow() {
    setBusy(true);
    setError(null);
    try {
      await api.admin.classifyNow(skill.skill_id);
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
        onClick={() => void classifyNow()}
        disabled={busy}
        className="rounded border border-ms-blue/30 px-3 py-1.5 text-xs font-medium text-ms-blue hover:bg-ms-blue/10 disabled:cursor-not-allowed disabled:opacity-50"
        title="Queue this skill for classifier retry now"
      >
        {busy ? "Queued…" : "Classify now"}
      </button>
      {error && <div className="text-[11px] text-danger-fg">{error}</div>}
      <span className="max-w-[220px] text-right text-[11px] text-muted">
        Queues this approved skill for classifier backfill without changing its
        published status.
      </span>
    </div>
  );
}
