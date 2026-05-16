"use client";

import { useMemo } from "react";

import { ProposalCard } from "@/components/curator/ProposalCard";
import { api } from "@/lib/api/client";
import { useResource } from "@/lib/hooks/useResource";
import type { ReviewProposal } from "@/lib/api/types";

export default function ReviewsQueuePage() {
  const { data, error, isLoading, mutate } = useResource(
    ["curator", "reviews", "pending"],
    () => api.curator.listReviews({ status: "pending" }),
  );

  const grouped = useMemo(() => {
    const map = new Map<string, ReviewProposal[]>();
    for (const p of data?.proposals ?? []) {
      const list = map.get(p.run_id) ?? [];
      list.push(p);
      map.set(p.run_id, list);
    }
    return Array.from(map.entries()).sort((a, b) =>
      a[0] < b[0] ? 1 : -1,
    );
  }, [data]);

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Review proposals</h2>
      <p className="text-sm text-gray-600">
        Pending LLM review proposals grouped by run. Approve a proposal to apply
        it (patch/merge) or accept it (keep). Reject to discard.
      </p>

      {error ? (
        <div className="ms-msgbar-danger">
          {String(error)}
        </div>
      ) : isLoading ? (
        <div className="h-16 animate-pulse rounded bg-gray-100" />
      ) : grouped.length === 0 ? (
        <p className="text-sm text-gray-500">No pending proposals.</p>
      ) : (
        <div className="space-y-6">
          {grouped.map(([runId, proposals]) => (
            <section key={runId} className="space-y-2">
              <h3 className="text-sm font-semibold">
                Run <code className="font-mono">{runId}</code>{" "}
                <span className="text-xs font-normal text-gray-500">
                  ({proposals.length} proposal
                  {proposals.length === 1 ? "" : "s"})
                </span>
              </h3>
              <div className="space-y-3">
                {proposals.map((p) => (
                  <ProposalCard
                    key={p.id}
                    proposal={p}
                    onChanged={() => void mutate()}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
