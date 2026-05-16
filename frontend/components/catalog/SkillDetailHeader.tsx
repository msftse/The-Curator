"use client";

import Link from "next/link";

import { StatusBadge } from "@/components/StatusBadge";
import type { SkillDetail } from "@/lib/api/types";

import { DownloadButton } from "./DownloadButton";
import { SkillDetailAdminActions } from "./SkillDetailAdminActions";

export function SkillDetailHeader({ skill }: { skill: SkillDetail }) {
  const hasBundle = skill.bundle !== null && skill.status === "approved";

  return (
    <header className="flex flex-col gap-4">
      <div className="text-xs">
        <Link href="/catalog" className="text-ms-blue hover:underline">
          ← Back to catalog
        </Link>
      </div>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <span className="ms-eyebrow-blue">Skill detail</span>
          <h1 className="font-display text-[28px] font-bold tracking-ms-display text-ink">
            {skill.name}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <span className="ms-chip font-mono">{skill.version}</span>
            <StatusBadge status={skill.status} />
            {skill.pinned && (
              <span className="ms-chip bg-gold/20 text-ink">📌 pinned</span>
            )}
          </div>
          <p className="text-sm text-muted">
            Uploaded by <strong className="text-ink-2">{skill.uploader}</strong>
            {skill.approved_at && (
              <>
                {" · "}approved <time>{skill.approved_at}</time>
              </>
            )}
          </p>
        </div>

        <div className="flex flex-col items-end gap-2">
          {hasBundle ? (
            <DownloadButton skillId={skill.skill_id} />
          ) : (
            <span className="rounded bg-bg-2 px-3 py-1.5 text-xs text-muted">
              Bundle not yet packaged
            </span>
          )}
          <code className="text-[11px] text-muted">{skill.skill_id}</code>
          <SkillDetailAdminActions skill={skill} />
        </div>
      </div>
    </header>
  );
}
