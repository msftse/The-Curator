"""Notification event model (M5-5).

Producers (M5-6) push instances of `NotificationEvent` onto Redis
`queue:notifications`. The notifier worker (`backend/workers/notifier.py`)
pops them, dedupes by `idempotency_key`, resolves recipients, renders a
template, and sends via Azure Communication Services.

Event type vocabulary is fixed at the eight values listed in
`.agents/plans/m5-defender-quarantine-notifier.md` §5. New event types
must update three places in lockstep: the `EventType` literal here, the
template pair in `backend/services/notifier/templates/`, and the
producer that emits it.

Idempotency: producers MUST compute `idempotency_key` deterministically
(typically `sha256(event_type + skill_id + version + extra)`); the
notifier uses Redis `SETNX notif:sent:{key} 1 EX 86400` to make replays
no-ops.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# The eight event types from the plan. Keep this list in lockstep with
# the on-disk template pairs in `backend/services/notifier/templates/`.
EventType = Literal[
    "skill.uploaded",
    "skill.awaiting_review",
    "skill.quarantined",
    "skill.approved",
    "skill.rejected",
    "defender.flagged",
    "admin.override",
    "curator.weekly_report",
]


# Subset of event types whose audience is *contributors* (the uploader of
# the skill) rather than admins. Everything else is admin-only.
CONTRIBUTOR_EVENTS: frozenset[str] = frozenset(
    {
        "skill.approved",
        "skill.rejected",
    }
)


class NotificationEvent(BaseModel):
    """Single email-worthy event.

    `skill_id` is optional because some events (e.g.
    `curator.weekly_report`) are not skill-scoped.

    `payload` is event-type-specific and rendered into the template; see
    individual templates for the keys they consume.

    `idempotency_key` short-circuits duplicate sends across worker
    restarts and producer retries. If the producer leaves it blank, the
    notifier derives one from `(event_type, skill_id, created_at)` —
    this is a fallback, not a substitute for the producer thinking about
    deduplication. Real producers (M5-6) compute it explicitly.
    """

    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    skill_id: str | None = None
    # Optional contributor email — used when `event_type in
    # CONTRIBUTOR_EVENTS` to address the message to the uploader rather
    # than the admin group. Producers SHOULD populate this for contributor
    # events; if missing, the notifier logs a warning and skips the send.
    contributor_email: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Set by the producer when it wants to give itself a per-message id
    # (separate from idempotency_key) for log correlation. Auto-filled if
    # blank.
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)

    def ensure_idempotency_key(self) -> str:
        """Return `idempotency_key`, filling it in if blank.

        Fallback derivation: `sha256(event_type|skill_id|created_at)`.
        Producers should set `idempotency_key` explicitly to make replay
        protection meaningful (e.g. include `version` and a producer-
        specific suffix).
        """
        if self.idempotency_key:
            return self.idempotency_key
        material = f"{self.event_type}|{self.skill_id or ''}|{self.created_at.isoformat()}"
        self.idempotency_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return self.idempotency_key

    def is_contributor_event(self) -> bool:
        return self.event_type in CONTRIBUTOR_EVENTS
