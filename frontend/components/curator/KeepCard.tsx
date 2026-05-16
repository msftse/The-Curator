import Link from "next/link";

import type { KeepPayload } from "@/lib/api/types";

export function KeepCard({ keep }: { keep: KeepPayload }) {
  return (
    <div className="rounded border border-info-border bg-info-bg p-3 text-sm">
      <div className="text-xs uppercase text-info-fg">Keep as-is</div>
      <div className="mt-1">
        Target:{" "}
        <Link
          href={`/admin/curator/skills`}
          className="font-mono text-ms-blue hover:underline"
        >
          {keep.target_skill_id}
        </Link>
      </div>
      {keep.rationale ? (
        <div className="mt-1 text-xs text-ink-2">
          Rationale: {keep.rationale}
        </div>
      ) : null}
    </div>
  );
}
