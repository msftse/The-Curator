// Typed DTOs — mirrors backend/models/api.py.
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
