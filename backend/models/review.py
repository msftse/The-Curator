"""M3 — LLM review proposal models.

Each ``ReviewProposal`` is a row in the ``review_proposals`` Cosmos container
(PK ``/run_id``). Proposals require **manager approval** to apply — see
``backend/services/curator_review_apply.py``. The review pass itself never
mutates a skill, and the apply path is the only place that does.

The never-delete invariant (AGENTS.md §5) is preserved end-to-end:

* ``kind="merge"`` does NOT delete merged-in skills — it archives them via the
  same Blob-copy + Cosmos status-flip path used by the M2 deterministic
  curator (``_copy_to_archive`` in ``backend/services/curator.py``).
* ``kind="patch"`` does NOT delete the previous bundle — it publishes a
  new version, leaving the old bytes in ``published/`` (defense in depth).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

ProposalKind = Literal["patch", "merge", "keep"]
ProposalStatus = Literal["pending", "approved", "applied", "rejected", "stale", "noop"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str = ""
    prompt_version: str = "v1"


class PatchPayload(BaseModel):
    target_skill_id: str
    target_version: str
    # ``patch_text`` is either a unified diff or a full SKILL.md replacement,
    # selected by ``replacement_mode``. M3 ships ``full_replace`` only;
    # ``unified_diff`` is reserved for a follow-up.
    patch_text: str
    replacement_mode: Literal["unified_diff", "full_replace"] = "full_replace"
    rationale: str = ""


class MergePayload(BaseModel):
    merged_skill_ids: list[str] = Field(min_length=2)
    proposed_umbrella_name: str
    proposed_umbrella_version: str = "1.0.0"
    proposed_umbrella_skill_md: str
    rationale: str = ""


class KeepPayload(BaseModel):
    target_skill_id: str
    rationale: str = ""


class ReviewProposal(BaseModel):
    """One row in the ``review_proposals`` Cosmos container (PK ``/run_id``)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    kind: ProposalKind
    status: ProposalStatus = "pending"
    created_at: datetime = Field(default_factory=_utc_now)
    created_by: str = "system:curator_review"

    # Snapshot of the inputs the model saw (used by the stale-etag check
    # at apply-time).
    target_skill_ids: list[str] = Field(default_factory=list)
    target_etags: dict[str, str] = Field(default_factory=dict)  # skill_id -> _etag
    input_hash: str = ""

    # Exactly one of the following is set, by ``kind``.
    patch: PatchPayload | None = None
    merge: MergePayload | None = None
    keep: KeepPayload | None = None

    usage: LLMUsage = Field(default_factory=LLMUsage)
    confidence: float = 0.0

    # Apply / reject lifecycle.
    approved_by: str | None = None
    approved_at: datetime | None = None
    applied_by: str | None = None
    applied_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    snapshot_name: str | None = None
    apply_error: str | None = None


class CuratorReviewRunRecord(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    candidates_considered: int = 0
    proposals_emitted: int = 0
    proposals_by_kind: dict[str, int] = Field(
        default_factory=lambda: {"patch": 0, "merge": 0, "keep": 0}
    )
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    provider: str = "foundry"
    model_id: str = ""
    prompt_version: str = "v1"
    aborted_reason: Literal["cost_cap", "lock", "paused", "provider_error"] | None = None
    lock_token: str | None = None


class ReviewListResponse(BaseModel):
    proposals: list[ReviewProposal]
    total: int
