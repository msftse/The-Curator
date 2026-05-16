"use client";

import Link from "next/link";

import { ApproveRejectBar } from "./ApproveRejectBar";
import { KeepCard } from "./KeepCard";
import { MergeSideBySide } from "./MergeSideBySide";
import { PatchDiffView } from "./PatchDiffView";

import type { ProposalKind, ReviewProposal } from "@/lib/api/types";

const KIND_COLORS: Record<ProposalKind, string> = {
  patch: "bg-amber-100 text-amber-800",
  merge: "bg-violet-100 text-violet-800",
  keep: "bg-sky-100 text-sky-800",
};

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function ProposalCard({
  proposal,
  onChanged,
  detailed = false,
}: {
  proposal: ReviewProposal;
  onChanged: (updated: ReviewProposal) => void;
  detailed?: boolean;
}) {
  const kindBadge = (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${KIND_COLORS[proposal.kind]}`}
    >
      {proposal.kind}
    </span>
  );

  return (
    <div className="space-y-3 rounded border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        {kindBadge}
        <span className="text-xs text-gray-500">
          confidence {Math.round(proposal.confidence * 100)}% ·{" "}
          {formatDate(proposal.created_at)}
        </span>
        <div className="ml-auto text-xs text-gray-500">
          targets:{" "}
          <code className="font-mono">
            {proposal.target_skill_ids.join(", ") || "—"}
          </code>
        </div>
      </div>

      {proposal.kind === "patch" && proposal.patch ? (
        <PatchDiffView patch={proposal.patch} />
      ) : null}
      {proposal.kind === "merge" && proposal.merge ? (
        <MergeSideBySide merge={proposal.merge} />
      ) : null}
      {proposal.kind === "keep" && proposal.keep ? (
        <KeepCard keep={proposal.keep} />
      ) : null}

      <ApproveRejectBar proposal={proposal} onChanged={onChanged} />

      {!detailed ? (
        <div className="text-right">
          <Link
            href={`/admin/curator/reviews/${encodeURIComponent(proposal.id)}?run_id=${encodeURIComponent(proposal.run_id)}`}
            className="text-xs text-sky-700 hover:underline"
          >
            Open detail →
          </Link>
        </div>
      ) : null}
    </div>
  );
}
