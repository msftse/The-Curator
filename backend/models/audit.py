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
    # M3 — Curator LLM review lifecycle.
    "patch_apply",
    "merge_apply",
    "review_run",
    "review_reject",
    # Entra migration — first observed admin sign-in per day (Redis SETNX, 24h TTL).
    "admin_session_start",
]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    skill_id: str
    action: AuditAction
    actor: str
    # Immutable Entra `oid` of the human actor when available. `None` for
    # `system:*` actors and stub-mode requests. Emails can be renamed by
    # tenant admins; oids cannot, so audits keyed on `actor_oid` survive
    # email churn.
    actor_oid: str | None = None
    at: datetime = Field(default_factory=_utc_now)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
