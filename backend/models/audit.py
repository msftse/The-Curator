"""Append-only audit record (PRD §10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AuditAction = Literal[
    "upload",
    "classify",
    "classify_failed",
    "approve",
    "reject",
    "publish",
    "archive",
    "stale",
    "pause",
    "resume",
    "pin",
    "unpin",
    "restore",
    "rollback",
    # M1 — API key lifecycle.
    "apikey_issue",
    "apikey_revoke",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    skill_id: str
    action: AuditAction
    actor: str
    at: datetime = Field(default_factory=_utc_now)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
