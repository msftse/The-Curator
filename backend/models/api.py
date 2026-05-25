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
    # Contributor-supplied hints from the upload form. Surfaced so the
    # detail page can show "uploader said X, classifier said Y" when they
    # diverge.
    user_category: str | None = None
    user_tags: list[str] = Field(default_factory=list)
    defender_status: str = "pending"
    defender_severity: str | None = None
    defender_report: dict[str, Any] | None = None
    defender_scanned_at: datetime | None = None


class SkillDetail(SkillListItem):
    """Single-skill response. Adds rendered SKILL.md body for the catalog detail page."""

    skill_md_text: str = ""

    # ---- Quarantine (mirror of SkillDoc fields; M5-3) ----
    # Surfaced so the catalog detail page can show "why was this killed?"
    # without a second Cosmos round-trip.
    quarantined_at: datetime | None = None
    quarantined_by: str | None = None
    quarantine_justification: str | None = None
    quarantine_expires_at: datetime | None = None


class DownloadUrlResponse(BaseModel):
    """Short-lived SAS URL for a published bundle.

    The URL itself is the capability — once issued, the browser hits Azure
    Blob directly (no further auth via the hub). Default TTL is 1 minute
    (see `backend.core.blob.signed_download_url`).
    """

    url: str
    expires_at: datetime


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class ArchiveRequest(BaseModel):
    """Body for admin manual archive (`POST /v1/admin/skills/{id}/archive`).

    `reason` is required — every state transition writes an audit row and
    the rationale lives there (matches the reject flow).
    """

    reason: str = Field(min_length=1, max_length=2000)


class ApproveRequest(BaseModel):
    # M5 defender gate: medium/high/critical findings cannot be approved
    # silently. Admins can either call the explicit defender-override endpoint
    # first, or pass this inline override on approve.
    defender_override: bool = False
    justification: str | None = Field(default=None, max_length=2000)


class QuarantineRequest(BaseModel):
    """Body for admin quarantine (`POST /v1/admin/skills/{id}/quarantine`).

    `justification` is required and must meet the configured minimum
    length (defaults to 20 chars, mirroring the defender override
    justification floor). The text is audit-logged verbatim, so admins
    should write the *why* — not "bad" — for the future operator who
    reads the trail.

    Length validation against `Settings.quarantine_min_justification_chars`
    happens at the service layer (this model just enforces non-empty and
    a generous upper bound).
    """

    justification: str = Field(min_length=1, max_length=2000)


class DefenderOverrideRequest(BaseModel):
    """Body for admin defender override (M5-4).

    `POST /v1/admin/skills/{id}/defender-override`.

    `justification` is required and must meet the configured minimum
    length (`Settings.quarantine_min_justification_chars`, default 20 —
    the same floor used by the quarantine endpoint per plan §3). The
    text is audit-logged verbatim, so the future operator who reads the
    trail learns *why* the admin disagreed with the scanner.

    The model enforces non-empty + a generous upper bound; the precise
    minimum-length check happens at the service layer (since the floor
    is settings-driven).
    """

    justification: str = Field(min_length=1, max_length=2000)


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
