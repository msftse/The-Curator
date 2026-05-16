"use client";

import Link from "next/link";

import type { SkillListItem } from "@/lib/api/types";

/**
 * Single skill card on the catalog grid.
 *
 * Read-only: clicking the card navigates to the detail page; no admin
 * affordances (no pin/unpin/restore) live here. That UI is in
 * `app/admin/curator/skills/`.
 */
export function CatalogCard({ skill }: { skill: SkillListItem }) {
  const cls = skill.classification;
  const quality = cls?.quality_score ?? null;
  const qualityTone =
    quality === null
      ? "bg-bg-2 text-muted"
      : quality >= 80
        ? "bg-gold/20 text-ink"
        : quality >= 50
          ? "bg-info-bg text-info-fg"
          : "bg-bg-2 text-muted";

  return (
    <Link
      href={`/catalog/${encodeURIComponent(skill.skill_id)}`}
      className="ms-card ms-card-hover ms-card-stripe group flex flex-col gap-3 p-5 hover:no-underline"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-display text-[17px] font-bold leading-tight text-ink">
          {skill.name}
        </h3>
        <span className="ms-chip shrink-0 font-mono text-[11px]">
          {skill.version}
        </span>
      </div>

      <p
        className="text-sm leading-[1.5] text-muted"
        style={{
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {cls?.summary || skill.description || "—"}
      </p>

      <div className="flex flex-wrap items-center gap-1.5">
        {cls?.category ? (
          <span className="ms-chip bg-violet/[0.18] text-violet-dark">
            {cls.category}
          </span>
        ) : (
          <span className="ms-chip text-muted">uncategorized</span>
        )}
        {(cls?.tags ?? []).slice(0, 4).map((t) => (
          <span key={t} className="ms-chip">
            {t}
          </span>
        ))}
        {(cls?.tags ?? []).length > 4 && (
          <span className="text-[11px] text-muted">
            +{(cls?.tags ?? []).length - 4}
          </span>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between gap-3 pt-2 text-xs">
        <span className="text-muted">by {skill.uploader}</span>
        <span
          className={
            "rounded-full px-2 py-0.5 text-[11px] font-semibold " + qualityTone
          }
        >
          {quality === null ? "—" : `Q ${quality}`}
        </span>
      </div>

      <span className="text-sm font-semibold text-ms-blue group-hover:no-underline">
        View →
      </span>
    </Link>
  );
}
