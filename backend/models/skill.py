"""Skill domain models matching PRD §10 schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SkillStatus = Literal["pending", "classified", "approved", "rejected", "stale", "archived"]
ClassifierStatus = Literal["queued", "running", "done", "failed"]


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

    # SKILL.md body, kept on the doc for the classifier worker + manager preview.
    skill_md_text: str = ""

    # M0 ONLY: raw uploaded tar bytes live here (base64) until publish.
    # M1 will replace with a `staging/` Blob container — see publish.py TODO.
    pending_bundle_b64: str | None = None
