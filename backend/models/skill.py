"""Skill domain models matching PRD §10 schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SkillStatus = Literal[
    "pending", "classified", "approved", "rejected", "stale", "archived", "quarantined"
]
ClassifierStatus = Literal["queued", "running", "done", "failed"]
# M5-2: defender state machine. `pending` → `scanning` → `clean`|`flagged`|`failed`.
# Worker re-tries `pending`/`failed` via the janitor sweep.
DefenderStatus = Literal["pending", "scanning", "clean", "flagged", "failed"]


# Canonical category taxonomy (PRD §7.2). The upload UI dropdown, the
# classifier allow-list, and any validation in between all read from here so
# there is exactly one source of truth.
#
# "uncategorized" is the default when neither the user nor the classifier
# could pick a fit; it is intentionally NOT offered in the upload dropdown.
CATEGORY_TAXONOMY: tuple[str, ...] = (
    "devops",
    "mlops",
    "productivity",
    "social-media",
    "research",
    "creative",
    "github",
    "other",
)

CATEGORY_UNCATEGORIZED: str = "uncategorized"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Classification(BaseModel):
    category: str = "uncategorized"
    tags: list[str] = Field(default_factory=list)
    quality_score: int = 0
    summary: str = ""
    duplicate_candidates: list[str] = Field(default_factory=list)
    classifier_version: str = "stub-v1"
    classified_at: datetime = Field(default_factory=_utc_now)


class Bundle(BaseModel):
    blob_url: str
    checksum_sha256: str
    size_bytes: int
    file_count: int


class UsageCounters(BaseModel):
    load_count: int = 0
    last_loaded_at: datetime | None = None
    loaders_30d: int = 0


class SkillDoc(BaseModel):
    """One Cosmos doc per skill version. PK is `skill_id`."""

    id: str
    skill_id: str
    version: str = "1.0.0"
    name: str
    description: str = ""
    status: SkillStatus = "pending"
    classifier_status: ClassifierStatus = "queued"

    uploader: str
    uploaded_at: datetime = Field(default_factory=_utc_now)
    approved_at: datetime | None = None
    approver: str | None = None
    rejection_reason: str | None = None

    classification: Classification | None = None
    bundle: Bundle | None = None
    usage: UsageCounters = Field(default_factory=UsageCounters)

    pinned: bool = False
    pinned_by: str | None = None

    # Contributor-supplied hints captured at upload. The classifier merges
    # these with its own output: user_category wins outright, user_tags are
    # union'd with the classifier's tags (user order first, dedup
    # case-insensitive, capped at 8). Kept as separate fields so the raw
    # classifier output stays inspectable on the doc.
    user_category: str | None = None
    user_tags: list[str] = Field(default_factory=list)

    # SKILL.md body, kept on the doc for the classifier worker + manager preview.
    skill_md_text: str = ""

    # M0 ONLY: raw uploaded tar bytes live here (base64) until publish.
    # M1 will replace with a `staging/` Blob container — see publish.py TODO.
    pending_bundle_b64: str | None = None

    # ---- Defender (M5-2) ----
    # `pending` is the initial state at upload time. Set to `scanning` while
    # the defender worker is running, then `clean` / `flagged` / `failed`.
    # `defender_report` is the inline DefenderReport.model_dump() output (or
    # None until the worker writes it). `defender_report_id` is reserved for
    # the future ``defender_reports`` container; M5-2 stores the report
    # inline and leaves the id null.
    defender_status: DefenderStatus = "pending"
    defender_severity: str | None = None  # mirror of report.overall_severity for filters
    defender_report: dict | None = None
    defender_report_id: str | None = None
    defender_scanned_at: datetime | None = None

    # ---- Quarantine (M5-3) ----
    # Set when an admin moves a defender-flagged skill into the terminal
    # `quarantine/` blob container. The justification text and actor are
    # *also* recorded on the immutable audit row — these mirrors exist on
    # the doc so the catalog detail page can show "why was this killed?"
    # without a second Cosmos round-trip. `quarantine_expires_at` is the
    # wall-clock deadline after which the quarantine janitor (the ONE
    # delete-after-N-days code path; AGENTS.md §5) deletes the bundle
    # bytes — the Cosmos doc itself is never deleted.
    quarantined_at: datetime | None = None
    quarantined_by: str | None = None
    quarantine_justification: str | None = None
    quarantine_expires_at: datetime | None = None
