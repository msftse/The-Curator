"use client";

import { MarkdownView } from "@/components/MarkdownView";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";
import type { MergePayload } from "@/lib/api/types";

function InputSkillColumn({ skillId }: { skillId: string }) {
  const { data, error, isLoading } = useResource(
    ["catalog", "get", skillId],
    () => api.catalog.get(skillId),
  );

  return (
    <div className="rounded border border-gray-200 p-3">
      <div className="mb-2 font-mono text-xs text-gray-600">{skillId}</div>
      {isLoading ? (
        <div className="h-24 animate-pulse rounded bg-gray-100" />
      ) : error || !data ? (
        <p className="text-xs italic text-gray-500">
          Original SKILL.md unavailable.
        </p>
      ) : (
        <>
          <div className="mb-1 text-sm font-medium">{data.name}</div>
          <MarkdownView source={data.description ?? ""} />
        </>
      )}
    </div>
  );
}

export function MergeSideBySide({ merge }: { merge: MergePayload }) {
  return (
    <div className="space-y-3">
      <div className="text-xs uppercase text-gray-500">
        Merge proposal — {merge.merged_skill_ids.length} inputs → 1 umbrella
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {merge.merged_skill_ids.map((sid) => (
          <InputSkillColumn key={sid} skillId={sid} />
        ))}
        <div className="rounded border border-success-border bg-success-bg p-3">
          <div className="mb-2 text-xs uppercase text-success-fg">
            Proposed umbrella
          </div>
          <div className="mb-1 text-sm font-medium">
            {merge.proposed_umbrella_name}{" "}
            <span className="text-xs text-gray-500">
              v{merge.proposed_umbrella_version}
            </span>
          </div>
          <MarkdownView source={merge.proposed_umbrella_skill_md} />
        </div>
      </div>
      {merge.rationale ? (
        <div className="text-xs text-gray-600">Rationale: {merge.rationale}</div>
      ) : null}
    </div>
  );
}
