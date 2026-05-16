"use client";

import type { SkillListItem } from "@/lib/api/types";

import { CatalogCard } from "./CatalogCard";

interface Props {
  skills: SkillListItem[];
  totalBeforeFilter: number;
}

export function CatalogGrid({ skills, totalBeforeFilter }: Props) {
  if (skills.length === 0) {
    return (
      <div className="ms-card flex flex-col items-center gap-3 px-6 py-16 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-bg-2 text-muted">
          <svg
            aria-hidden
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
        </div>
        <div className="font-display text-sm font-semibold text-ink">
          {totalBeforeFilter === 0
            ? "No approved skills yet"
            : "No skills match these filters"}
        </div>
        <p className="max-w-xs text-xs text-muted">
          {totalBeforeFilter === 0
            ? "Once a manager approves a submission it will appear here for the team to discover."
            : "Try clearing filters or broadening your search."}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted">
        Showing <strong className="text-ink-2">{skills.length}</strong> of{" "}
        <strong className="text-ink-2">{totalBeforeFilter}</strong> approved
        skill{totalBeforeFilter === 1 ? "" : "s"}.
      </p>
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {skills.map((s) => (
          <CatalogCard key={`${s.skill_id}:${s.version}`} skill={s} />
        ))}
      </div>
    </div>
  );
}
