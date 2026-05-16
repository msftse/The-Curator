// Typed DTOs — mirrors backend/models/api.py + backend/models/curator.py + backend/models/review.py.
export type SkillStatus =
  | "pending"
  | "classified"
  | "approved"
  | "rejected"
  | "stale"
  | "archived";

export type ClassifierStatus = "queued" | "running" | "done" | "failed";

export interface Classification {
  category: string;
  tags: string[];
  quality_score: number;
  summary: string;
  duplicate_candidates: string[];
  classifier_version: string;
  classified_at: string;
}

export interface Bundle {
  blob_url: string;
  checksum_sha256: string;
  size_bytes: number;
  file_count: number;
}

export interface SkillListItem {
  skill_id: string;
  version: string;
  name: string;
  description: string;
  status: SkillStatus;
  classifier_status: ClassifierStatus;
  uploader: string;
  uploaded_at: string;
  approved_at: string | null;
  classification: Classification | null;
  bundle: Bundle | null;
  pinned: boolean;
}

export interface UploadResponse {
  skill_id: string;
  version: string;
  status: SkillStatus;
  classifier_status: ClassifierStatus;
  uploaded_at: string;
}

export interface ClassificationPatch {
  category?: string;
  tags?: string[];
  quality_score?: number;
  summary?: string;
  duplicate_candidates?: string[];
}

export interface ApiError {
  error_code: string;
  message: string;
}

// ---- Curator (M2) -------------------------------------------------------

export type TransitionReason =
  | "steady_state"
  | "stale_30d"
  | "archive_90d"
  | "pinned"
  | "missing_usage_data";

export interface Transition {
  skill_id: string;
  version: string;
  before: SkillStatus;
  after: SkillStatus;
  reason: TransitionReason;
  applied: boolean;
}

export interface SnapshotManifestEntry {
  skill_id: string;
  version: string;
  status: SkillStatus;
  checksum_sha256: string;
  blob_path: string;
}

export interface SnapshotManifest {
  run_id: string;
  captured_at: string;
  skills: SnapshotManifestEntry[];
}

export interface CuratorRunRecord {
  run_id: string;
  started_at: string;
  finished_at: string;
  dry_run: boolean;
  planner_inputs: Record<string, unknown>;
  transitions: Transition[];
  skipped_pinned: string[];
  snapshot_name: string | null;
  lock_token: string | null;
}

export interface RollbackResult {
  snapshot_name: string;
  pre_rollback_snapshot_name: string;
  restored: Transition[];
  at: string;
}

export interface CuratorStatus {
  paused: boolean;
  lock_held: boolean;
  last_run: CuratorRunRecord | null;
  schedule_enabled: boolean;
  schedule_next: string | null;
}

// Optional response shape for an as-yet-unimplemented snapshot listing endpoint.
export interface SnapshotListItem {
  name: string;
  captured_at: string;
  skills_count: number;
  size_bytes: number;
}

// ---- Curator review (M3) ------------------------------------------------

export type ProposalKind = "patch" | "merge" | "keep";
export type ProposalStatus =
  | "pending"
  | "approved"
  | "applied"
  | "rejected"
  | "stale"
  | "noop";

export interface LLMUsage {
  input_tokens: number;
  output_tokens: number;
  model_id: string;
  prompt_version: string;
}

export interface PatchPayload {
  target_skill_id: string;
  target_version: string;
  patch_text: string;
  replacement_mode: "unified_diff" | "full_replace";
  rationale: string;
}

export interface MergePayload {
  merged_skill_ids: string[];
  proposed_umbrella_name: string;
  proposed_umbrella_version: string;
  proposed_umbrella_skill_md: string;
  rationale: string;
}

export interface KeepPayload {
  target_skill_id: string;
  rationale: string;
}

export interface ReviewProposal {
  id: string;
  run_id: string;
  kind: ProposalKind;
  status: ProposalStatus;
  created_at: string;
  created_by: string;
  target_skill_ids: string[];
  target_etags: Record<string, string>;
  input_hash: string;
  patch: PatchPayload | null;
  merge: MergePayload | null;
  keep: KeepPayload | null;
  usage: LLMUsage;
  confidence: number;
  approved_by: string | null;
  approved_at: string | null;
  applied_by: string | null;
  applied_at: string | null;
  rejected_by: string | null;
  rejected_at: string | null;
  rejection_reason: string | null;
  snapshot_name: string | null;
  apply_error: string | null;
}

export interface CuratorReviewRunRecord {
  run_id: string;
  started_at: string;
  finished_at: string;
  candidates_considered: number;
  proposals_emitted: number;
  proposals_by_kind: Record<string, number>;
  total_input_tokens: number;
  total_output_tokens: number;
  provider: string;
  model_id: string;
  prompt_version: string;
  aborted_reason: "cost_cap" | "lock" | "paused" | "provider_error" | null;
  lock_token: string | null;
}

export interface ReviewListResponse {
  proposals: ReviewProposal[];
  total: number;
}
