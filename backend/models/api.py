"""Request/response DTOs. Mirror what the frontend consumes.

Keep these decoupled from `SkillDoc` so we don't accidentally leak Cosmos
internals (`_etag`, `_rid`, `pending_bundle_b64`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.models.skill import Bundle, Classification, ClassifierStatus, SkillStatus


class UploadResponse(BaseModel):
    skill_id: str
    version: str
    status: SkillStatus
    classifier_status: ClassifierStatus
    uploaded_at: datetime


class SkillListItem(BaseModel):
    skill_id: str
    version: str
    name: str
    description: str
    status: SkillStatus
    classifier_status: ClassifierStatus
    uploader: str
    uploaded_at: datetime
    approved_at: datetime | None = None
    classification: Classification | None = None
    bundle: Bundle | None = None
    pinned: bool = False


class SkillDetail(SkillListItem):
    """Single-skill response. Adds rendered SKILL.md body for the catalog detail page."""

    skill_md_text: str = ""


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ArchiveRequest(BaseModel):
    """Body for admin manual archive (`POST /v1/admin/skills/{id}/archive`).

    `reason` is required — every state transition writes an audit row and
    the rationale lives there (matches the reject flow).
    """

    reason: str = Field(min_length=1, max_length=2000)


class ApproveRequest(BaseModel):
    # M0 has no fields, but reserved for M1 (e.g. force_republish).
    pass


class ClassificationPatch(BaseModel):
    category: str | None = None
    tags: list[str] | None = None
    quality_score: int | None = None
    summary: str | None = None
    duplicate_candidates: list[str] | None = None


class HealthResponse(BaseModel):
    ok: bool
    cosmos: str
    redis: str
    blob: str
    details: dict[str, Any] = Field(default_factory=dict)
