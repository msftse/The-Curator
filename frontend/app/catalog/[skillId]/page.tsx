"use client";

import Link from "next/link";
import { use } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { SkillDetailHeader } from "@/components/catalog/SkillDetailHeader";
import { SkillDetailMeta } from "@/components/catalog/SkillDetailMeta";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";

export default function CatalogSkillDetailPage({
  params,
}: {
  params: Promise<{ skillId: string }>;
}) {
  const { skillId: raw } = use(params);
  const skillId = decodeURIComponent(raw);

  const { data, error, isLoading } = useResource(
    ["catalog", "skill", skillId],
    () => api.catalog.get(skillId),
  );

  const errMsg = error ? String(error) : null;
  const is404 = errMsg?.includes("API 404") ?? false;

  return (
    <div className="mx-auto flex max-w-[1100px] flex-col gap-6 px-6 py-12">
      {is404 ? (
        <div className="ms-card flex flex-col items-start gap-3 p-6">
          <h1 className="font-display text-xl font-bold text-ink">
            Skill not found
          </h1>
          <p className="text-sm text-muted">
            This skill may have been archived, or the ID is incorrect.
          </p>
          <Link href="/catalog" className="text-sm font-semibold text-ms-blue">
            ← Back to catalog
          </Link>
        </div>
      ) : errMsg ? (
        <div className="ms-msgbar-danger">
          <span>{errMsg}</span>
        </div>
      ) : !data ? (
        <div className="text-sm text-muted">
          {isLoading ? "Loading skill…" : "No data."}
        </div>
      ) : (
        <>
          <SkillDetailHeader skill={data} />
          <SkillDetailMeta skill={data} />
          <div className="ms-card p-6">
            <h2 className="mb-3 font-display text-sm font-bold uppercase tracking-[0.15em] text-ink-2">
              SKILL.md
            </h2>
            {(data.skill_md_text ?? "").trim() ? (
              <MarkdownView source={data.skill_md_text ?? ""} />
            ) : (
              <p className="text-sm italic text-muted">
                No SKILL.md body recorded for this skill.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
