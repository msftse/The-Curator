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
    # M5-3 — admin moved a defender-flagged skill to the quarantine
    # container. Terminal status. Bundle bytes live in quarantine/ until
    # the quarantine janitor deletes them after `QUARANTINE_RETENTION_DAYS`
    # (the ONE allowed delete-after-N-days code path in the system; see
    # AGENTS.md §5).
    "quarantine",
    "quarantine_delete",
    # M5-4 — admin overrode a defender medium/high finding with a
    # justification. Flips the skill back to the normal review pipeline
    # (defender_status=clean) so the existing approve flow can run. The
    # original defender_report + severity are preserved on the doc; the
    # override is the audit row's responsibility.
    "defender_override",
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
    # M5-7 — admin edited the curator CronJob schedule via the admin UI.
    # Cosmos `system_state` (key=curator_schedule) is the source of truth;
    # the reconciler worker patches the K8s CronJob spec to match.
    "curator_schedule_update",
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
