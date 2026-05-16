import Link from "next/link";

import type { KeepPayload } from "@/lib/api/types";

export function KeepCard({ keep }: { keep: KeepPayload }) {
  return (
    <div className="rounded border border-sky-200 bg-sky-50 p-3 text-sm">
      <div className="text-xs uppercase text-sky-700">Keep as-is</div>
      <div className="mt-1">
        Target:{" "}
        <Link
          href={`/admin/curator/skills`}
          className="font-mono text-sky-800 hover:underline"
        >
          {keep.target_skill_id}
        </Link>
      </div>
      {keep.rationale ? (
        <div className="mt-1 text-xs text-gray-700">
          Rationale: {keep.rationale}
        </div>
      ) : null}
    </div>
  );
}
