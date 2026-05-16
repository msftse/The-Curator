"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { ProposalCard } from "@/components/curator/ProposalCard";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";

export default function ReviewProposalDetailPage({
  params,
}: {
  params: { proposalId: string };
}) {
  const proposalId = decodeURIComponent(params.proposalId);
  const search = useSearchParams();
  const runId = search?.get("run_id") ?? "";

  const { data, error, isLoading, mutate } = useResource(
    ["curator", "review", proposalId, runId],
    () => api.curator.getReview(proposalId, runId),
  );

  return (
    <div className="space-y-4">
      <div className="text-xs">
        <Link
          href="/admin/curator/reviews"
          className="text-sky-700 hover:underline"
        >
          ← back to queue
        </Link>
      </div>

      {!runId ? (
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          Missing <code>run_id</code> query parameter.
        </div>
      ) : error ? (
        <div className="rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">
          {String(error)}
        </div>
      ) : isLoading || !data ? (
        <div className="h-32 animate-pulse rounded bg-gray-100" />
      ) : (
        <ProposalCard
          proposal={data}
          detailed
          onChanged={() => void mutate()}
        />
      )}
    </div>
  );
}
