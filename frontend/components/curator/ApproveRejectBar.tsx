"use client";

import { useState } from "react";

import { Confirm } from "@/components/Confirm";
import { api } from "@/lib/api/client";
import type { ReviewProposal } from "@/lib/api/types";

export function ApproveRejectBar({
  proposal,
  onChanged,
}: {
  proposal: ReviewProposal;
  onChanged: (updated: ReviewProposal) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmMerge, setConfirmMerge] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  async function doApprove() {
    setBusy("approve");
    setError(null);
    try {
      const updated = await api.curator.approveReview(
        proposal.id,
        proposal.run_id,
      );
      onChanged(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
      setConfirmMerge(false);
    }
  }

  async function doReject() {
    setBusy("reject");
    setError(null);
    try {
      const updated = await api.curator.rejectReview(
        proposal.id,
        proposal.run_id,
        reason,
      );
      onChanged(updated);
      setRejecting(false);
      setReason("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  if (proposal.status !== "pending") {
    return (
      <div className="rounded border border-gray-200 bg-gray-50 p-2 text-xs text-gray-600">
        Status: <code>{proposal.status}</code>
        {proposal.rejection_reason ? (
          <span> · reason: {proposal.rejection_reason}</span>
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <button
          disabled={busy !== null}
          onClick={() =>
            proposal.kind === "merge" ? setConfirmMerge(true) : void doApprove()
          }
          className="rounded bg-ms-green px-3 py-1 text-sm text-white hover:brightness-95 disabled:opacity-50"
        >
          Approve
        </button>
        <button
          disabled={busy !== null}
          onClick={() => setRejecting((v) => !v)}
          className="rounded bg-ms-red px-3 py-1 text-sm text-white hover:brightness-95 disabled:opacity-50"
        >
          Reject
        </button>
      </div>
      {rejecting ? (
        <div className="space-y-2 rounded border border-danger-border bg-danger-bg p-2">
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Optional reason"
            className="block w-full rounded border border-line-2 px-2 py-1 text-xs"
            rows={3}
          />
          <button
            disabled={busy !== null}
            onClick={() => void doReject()}
            className="rounded bg-ms-red px-3 py-1 text-xs text-white hover:brightness-95 disabled:opacity-50"
          >
            Confirm reject
          </button>
        </div>
      ) : null}
      {error ? (
        <div className="ms-msgbar-danger text-xs">
          {error}
        </div>
      ) : null}

      <Confirm
        open={confirmMerge}
        title="Apply merge proposal?"
        body={
          <div>
            This will archive {proposal.merge?.merged_skill_ids.length ?? 0}{" "}
            skills under a new umbrella. The archived skills are recoverable via
            rollback or per-skill restore — nothing is deleted.
          </div>
        }
        confirmText="Apply merge"
        onConfirm={doApprove}
        onClose={() => setConfirmMerge(false)}
      />
    </div>
  );
}
