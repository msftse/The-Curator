"use client";

import Link from "next/link";
import { use } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { DefenderReportPanel } from "@/components/catalog/DefenderReportPanel";
import { SkillDetailDefenderActions } from "@/components/catalog/SkillDetailDefenderActions";
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

  if (is404) {
    return (
      <div className="mx-auto max-w-[1100px] px-6 py-12">
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
      </div>
    );
  }

  if (errMsg) {
    return (
      <div className="mx-auto max-w-[1100px] px-6 py-12">
        <div className="ms-msgbar-danger">
          <span>{errMsg}</span>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="mx-auto max-w-[1100px] px-6 py-12 text-sm text-muted">
        {isLoading ? "Loading skill…" : "No data."}
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-8 px-6 py-12">
      <SkillDetailHeader skill={data} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <article className="ms-card p-7">
          <div className="mb-5 flex items-center justify-between border-b border-line pb-3">
            <h2 className="font-display text-[11px] font-bold uppercase tracking-[0.18em] text-ink-2">
              SKILL.md
            </h2>
            <code className="font-mono text-[11px] text-muted">
              {data.skill_id}@{data.version}
            </code>
          </div>
          <MarkdownView source={data.skill_md_text ?? ""} />
        </article>

        <div className="lg:sticky lg:top-6 lg:self-start">
          <div className="flex flex-col gap-4">
            <SkillDetailDefenderActions skill={data} />
            <DefenderReportPanel
              status={data.defender_status}
              severity={data.defender_severity}
              report={data.defender_report}
              scannedAt={data.defender_scanned_at}
            />
            <SkillDetailMeta skill={data} />
          </div>
        </div>
      </div>
    </div>
  );
}
