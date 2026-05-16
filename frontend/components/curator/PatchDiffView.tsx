"use client";

import { useState } from "react";

import { MarkdownView } from "@/components/MarkdownView";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";
import type { PatchPayload } from "@/lib/api/types";

function DiffPre({ text }: { text: string }) {
  return (
    <pre className="overflow-x-auto rounded bg-gray-900 p-3 text-xs leading-snug">
      {text.split("\n").map((line, i) => {
        let cls = "text-gray-300";
        if (line.startsWith("+") && !line.startsWith("+++"))
          cls = "text-emerald-300";
        else if (line.startsWith("-") && !line.startsWith("---"))
          cls = "text-rose-300";
        else if (line.startsWith("@@")) cls = "text-sky-300";
        return (
          <div key={i} className={cls}>
            {line || "\u00A0"}
          </div>
        );
      })}
    </pre>
  );
}

export function PatchDiffView({ patch }: { patch: PatchPayload }) {
  const [showOriginal, setShowOriginal] = useState(true);
  const { data, error, isLoading } = useResource(
    ["catalog", "get", patch.target_skill_id],
    () => api.catalog.get(patch.target_skill_id),
  );

  if (patch.replacement_mode === "unified_diff") {
    return (
      <div className="space-y-2">
        <div className="text-xs uppercase text-gray-500">
          Unified diff against {patch.target_skill_id}@{patch.target_version}
        </div>
        <DiffPre text={patch.patch_text} />
        {patch.rationale ? (
          <div className="text-xs text-gray-600">
            Rationale: {patch.rationale}
          </div>
        ) : null}
      </div>
    );
  }

  // full_replace: side-by-side original vs proposed
  const originalAvailable = !isLoading && !error && data;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase text-gray-500">
          Full replacement for {patch.target_skill_id}@{patch.target_version}
        </div>
        <label className="text-xs">
          <input
            type="checkbox"
            checked={showOriginal}
            onChange={(e) => setShowOriginal(e.target.checked)}
            className="mr-1"
          />
          Show original
        </label>
      </div>
      <div
        className={`grid gap-3 ${
          showOriginal ? "grid-cols-1 md:grid-cols-2" : "grid-cols-1"
        }`}
      >
        {showOriginal ? (
          <div className="rounded border border-gray-200 p-3">
            <div className="mb-2 text-xs uppercase text-gray-500">Original</div>
            {isLoading ? (
              <div className="h-24 animate-pulse rounded bg-gray-100" />
            ) : !originalAvailable ? (
              <p className="text-xs italic text-gray-500">
                Original SKILL.md unavailable.
              </p>
            ) : (
              <MarkdownView source={data.description ?? ""} />
            )}
          </div>
        ) : null}
        <div className="rounded border border-emerald-200 p-3">
          <div className="mb-2 text-xs uppercase text-emerald-700">Proposed</div>
          <MarkdownView source={patch.patch_text} />
        </div>
      </div>
      {patch.rationale ? (
        <div className="text-xs text-gray-600">Rationale: {patch.rationale}</div>
      ) : null}
    </div>
  );
}
