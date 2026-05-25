"use client";

import Link from "next/link";

import { StatusBadge } from "@/components/StatusBadge";
import type { SkillDetail } from "@/lib/api/types";

import { DownloadButton } from "./DownloadButton";
import { SkillDetailAdminActions } from "./SkillDetailAdminActions";
import { SkillDetailClassifierActions } from "./SkillDetailClassifierActions";
import { SkillDetailDefenderRescan } from "./SkillDetailDefenderRescan";

export function SkillDetailHeader({ skill }: { skill: SkillDetail }) {
  const hasBundle = skill.bundle !== null && skill.status === "approved";
  // Effective category = whatever the contributor said, else classifier output.
  // The merge already happened server-side, so `classification.category` is
  // already the merged value. We still surface `user_category` separately
  // when set, so reviewers can see uploader intent.
  const effectiveCategory =
    skill.user_category ?? skill.classification?.category ?? null;
  const effectiveTags = skill.classification?.tags ?? [];
  const description =
    skill.description?.trim() || skill.classification?.summary?.trim() || "";

  return (
    <header className="flex flex-col gap-5">
      <div className="text-xs">
        <Link href="/catalog" className="text-ms-blue hover:underline">
          ← Back to catalog
        </Link>
      </div>

      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="ms-eyebrow-blue">Skill detail</span>
            {effectiveCategory && (
              <span className="ms-chip bg-violet/[0.18] text-violet-dark">
                {effectiveCategory}
              </span>
            )}
            <StatusBadge status={skill.status} />
            <span className="ms-chip font-mono">{skill.version}</span>
            {skill.pinned && (
              <span className="ms-chip bg-gold/20 text-ink">📌 pinned</span>
            )}
          </div>

          <h1 className="font-display text-[clamp(26px,3.5vw,36px)] font-bold leading-tight tracking-ms-display text-ink">
            {skill.name}
          </h1>

          {description && (
            <p className="max-w-2xl text-[15px] leading-relaxed text-ink-2">
              {description}
            </p>
          )}

          {effectiveTags.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 pt-1">
              {effectiveTags.map((t) => (
                <span key={t} className="ms-chip">
                  {t}
                </span>
              ))}
            </div>
          )}

          <p className="pt-1 text-xs text-muted">
            Uploaded by <strong className="text-ink-2">{skill.uploader}</strong>
            {skill.approved_at && (
              <>
                {" · "}approved <time>{skill.approved_at}</time>
              </>
            )}
            {" · "}
            <code className="font-mono text-[11px]">{skill.skill_id}</code>
          </p>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2">
          {hasBundle ? (
            <DownloadButton
              skillId={skill.skill_id}
              skillName={skill.name}
              uploader={skill.uploader}
              version={skill.version}
              category={effectiveCategory}
              description={description}
              tags={effectiveTags}
            />
          ) : (
            <span className="rounded bg-bg-2 px-3 py-1.5 text-xs text-muted">
              Bundle not yet packaged
            </span>
          )}
          <SkillDetailClassifierActions skill={skill} />
          <SkillDetailDefenderRescan skill={skill} />
          <SkillDetailAdminActions skill={skill} />
        </div>
      </div>
    </header>
  );
}
