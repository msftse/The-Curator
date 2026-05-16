"""Curator (M2) domain models.

Request/response DTOs and persisted record shapes for the usage pipeline,
deterministic planner, snapshots, rollback, and admin status surfaces.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.models.skill import SkillStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


TransitionReason = Literal[
    "steady_state",
    "stale_30d",
    "archive_90d",
    "pinned",
    "missing_usage_data",
]


# ---- Usage pipeline -----------------------------------------------------


class UsageEvent(BaseModel):
    """Request body for POST /v1/skills/{id}/usage."""

    loader_id: str = Field(min_length=1, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)


class UsageEventDoc(BaseModel):
    """One row in the `usage_events` Cosmos container (PK /skill_id, TTL 90d)."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    skill_id: str
    version: str
    loader_id: str
    at: datetime = Field(default_factory=_utc_now)
    context: dict[str, Any] = Field(default_factory=dict)


# ---- Curator planner / executor -----------------------------------------


class Transition(BaseModel):
    skill_id: str
    version: str
    before: SkillStatus
    after: SkillStatus
    reason: TransitionReason
    applied: bool = False


class SnapshotManifestEntry(BaseModel):
    skill_id: str
    version: str
    status: SkillStatus
    checksum_sha256: str
    blob_path: str


class SnapshotManifest(BaseModel):
    run_id: str
    captured_at: datetime = Field(default_factory=_utc_now)
    skills: list[SnapshotManifestEntry] = Field(default_factory=list)


class CuratorRunRecord(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    planner_inputs: dict[str, Any] = Field(default_factory=dict)
    transitions: list[Transition] = Field(default_factory=list)
    skipped_pinned: list[str] = Field(default_factory=list)
    snapshot_name: str | None = None
    lock_token: str | None = None


class RollbackResult(BaseModel):
    snapshot_name: str
    pre_rollback_snapshot_name: str
    restored: list[Transition] = Field(default_factory=list)
    at: datetime = Field(default_factory=_utc_now)


class CuratorStatus(BaseModel):
    paused: bool
    lock_held: bool
    last_run: CuratorRunRecord | None = None
    schedule_enabled: bool = True
    schedule_next: datetime | None = None
