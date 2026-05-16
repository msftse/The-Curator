"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { use } from "react";

import { ProposalCard } from "@/components/curator/ProposalCard";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";

export default function ReviewProposalDetailPage({
  params,
}: {
  params: Promise<{ proposalId: string }>;
}) {
  const { proposalId: rawProposalId } = use(params);
  const proposalId = decodeURIComponent(rawProposalId);
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
          className="text-ms-blue hover:underline"
        >
          ← back to queue
        </Link>
      </div>

      {!runId ? (
        <div className="rounded ms-msgbar-danger">
          Missing <code>run_id</code> query parameter.
        </div>
      ) : error ? (
        <div className="rounded ms-msgbar-danger">
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
