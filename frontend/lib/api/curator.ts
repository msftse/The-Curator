// Typed client for /v1/admin/curator/* endpoints.
import { call, callText } from "./client";
import type {
  CuratorRunRecord,
  CuratorStatus,
  ReviewListResponse,
  ReviewProposal,
  RollbackResult,
  SkillListItem,
  SnapshotListItem,
} from "./types";

export const curator = {
  status(): Promise<CuratorStatus> {
    return call<CuratorStatus>("/v1/admin/curator/status");
  },
  pause(): Promise<CuratorStatus> {
    return call<CuratorStatus>("/v1/admin/curator/pause", { method: "POST" });
  },
  resume(): Promise<CuratorStatus> {
    return call<CuratorStatus>("/v1/admin/curator/resume", { method: "POST" });
  },
  run(opts: { dryRun?: boolean } = {}): Promise<CuratorRunRecord> {
    const q = opts.dryRun ? "?dry_run=true" : "";
    return call<CuratorRunRecord>(`/v1/admin/curator/run${q}`, {
      method: "POST",
    });
  },
  rollback(opts: { id?: string } = {}): Promise<RollbackResult> {
    const q = opts.id ? `?id=${encodeURIComponent(opts.id)}` : "";
    return call<RollbackResult>(`/v1/admin/curator/rollback${q}`, {
      method: "POST",
    });
  },
  pin(skillId: string): Promise<SkillListItem> {
    return call<SkillListItem>(
      `/v1/admin/curator/pin/${encodeURIComponent(skillId)}`,
      { method: "POST" },
    );
  },
  unpin(skillId: string): Promise<SkillListItem> {
    return call<SkillListItem>(
      `/v1/admin/curator/unpin/${encodeURIComponent(skillId)}`,
      { method: "POST" },
    );
  },
  restore(skillId: string): Promise<SkillListItem> {
    return call<SkillListItem>(
      `/v1/admin/curator/restore/${encodeURIComponent(skillId)}`,
      { method: "POST" },
    );
  },
  // ---- Not-yet-implemented backend endpoints ----------------------------
  // The plan flags these as Phase 1 backend additions that this PR does not
  // include. The UI catches the resulting failure and renders a placeholder.
  listSnapshots(): Promise<SnapshotListItem[]> {
    return call<SnapshotListItem[]>("/v1/admin/curator/snapshots");
  },
  listRuns(opts: { limit?: number } = {}): Promise<CuratorRunRecord[]> {
    const q = opts.limit ? `?limit=${opts.limit}` : "";
    return call<CuratorRunRecord[]>(`/v1/admin/curator/runs${q}`);
  },
  getRunReport(runId: string): Promise<string> {
    return callText(
      `/v1/admin/curator/runs/${encodeURIComponent(runId)}/report`,
    );
  },
  // ---- M3 review proposals ---------------------------------------------
  listReviews(
    opts: { status?: string; runId?: string; limit?: number } = {},
  ): Promise<ReviewListResponse> {
    const params = new URLSearchParams();
    if (opts.status) params.set("status", opts.status);
    if (opts.runId) params.set("run_id", opts.runId);
    if (opts.limit) params.set("limit", String(opts.limit));
    const q = params.toString();
    return call<ReviewListResponse>(
      `/v1/admin/curator/reviews${q ? `?${q}` : ""}`,
    );
  },
  getReview(proposalId: string, runId: string): Promise<ReviewProposal> {
    return call<ReviewProposal>(
      `/v1/admin/curator/reviews/${encodeURIComponent(proposalId)}?run_id=${encodeURIComponent(runId)}`,
    );
  },
  approveReview(proposalId: string, runId: string): Promise<ReviewProposal> {
    return call<ReviewProposal>(
      `/v1/admin/curator/reviews/${encodeURIComponent(proposalId)}/approve?run_id=${encodeURIComponent(runId)}`,
      { method: "POST" },
    );
  },
  rejectReview(
    proposalId: string,
    runId: string,
    reason = "",
  ): Promise<ReviewProposal> {
    const params = new URLSearchParams({ run_id: runId });
    if (reason) params.set("reason", reason);
    return call<ReviewProposal>(
      `/v1/admin/curator/reviews/${encodeURIComponent(proposalId)}/reject?${params.toString()}`,
      { method: "POST" },
    );
  },
};
