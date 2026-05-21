"""Notification producers (M5-6).

A single helper, `enqueue_notification(event, *, redis)`, that pushes a
serialized `NotificationEvent` onto Redis `queue:notifications`. The
notifier worker (M5-5) is the sole consumer.

Producers are **fire-and-forget**. Every call site MUST:

1. Have already written the motivating change to Cosmos (AGENTS.md §4
   rule #1 — Cosmos-first). The notification is downstream of the
   durable state; if the queue push fails the truth still survives.
2. Swallow exceptions. `enqueue_notification` already does this
   internally — it logs on failure and returns False rather than
   raising — so callers can simply ``await enqueue_notification(...)``
   without a try/except. The return value is exposed for tests that
   want to assert the push succeeded.

Why a helper instead of inline `redis.rpush(... json.dumps(...))` at
every site?

* One place owns the queue key + the JSON shape. The defender worker's
  M5-2 placeholder push (a hand-rolled dict) is the canonical example
  of how that goes wrong — the M5-5 notifier worker can't deserialize
  it. Centralising the shape behind `NotificationEvent.model_dump_json`
  keeps producers and consumer in lockstep.
* Idempotency keys are easy to forget. The helper has a
  `make_idempotency_key(...)` convenience so producers don't reinvent
  the SHA256 dance.
* AGENTS.md §4 rule #4 — the queue is the one allowed Redis "in-flight"
  exception. Centralising the push lets us add the janitor sweep later
  without revisiting every call site.
"""

from __future__ import annotations

import hashlib
from typing import Any

from redis.asyncio import Redis

from backend.core.logging import get_logger
from backend.core.redis import key_queue_notifications
from backend.models.notifications import EventType, NotificationEvent

log = get_logger(__name__)


def make_idempotency_key(
    event_type: EventType,
    *,
    skill_id: str | None = None,
    version: str | None = None,
    extra: str | None = None,
) -> str:
    """Deterministic SHA256 over the identifying fields.

    Producers SHOULD pass the same `(event_type, skill_id, version,
    extra)` for what is conceptually the same event — that way a
    classifier retry, a worker restart, or a janitor re-enqueue all
    collapse to a single send via the notifier's Redis SETNX dedupe.

    `extra` is the lever for per-producer disambiguation when the
    other three fields aren't enough (e.g. the curator weekly digest
    keys on the run_id so weekly emails don't dedupe each other).
    """
    parts = [
        event_type,
        skill_id or "",
        version or "",
        extra or "",
    ]
    material = "|".join(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


async def enqueue_notification(
    event: NotificationEvent,
    *,
    redis: Redis,
) -> bool:
    """Push `event` onto `queue:notifications`. Never raises.

    Returns True on success, False on any failure (Redis down, JSON
    encode error, etc). Callers MUST treat the return as advisory
    only — the durable Cosmos write that triggered the event is the
    source of truth.

    Logs every failure at WARNING with the event_type + skill_id so
    operators can correlate missing emails with queue errors.
    """
    # Fill in idempotency_key if the producer forgot. This is a defence-
    # in-depth: producers SHOULD set it explicitly via
    # `make_idempotency_key`, but the fallback here keeps the notifier
    # from no-op'ing on the next replay.
    event.ensure_idempotency_key()

    try:
        payload = event.model_dump_json()
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "notifier.enqueue_serialize_failed",
            extra={
                "event_type": event.event_type,
                "skill_id": event.skill_id,
                "err": str(exc),
            },
        )
        return False

    try:
        await redis.rpush(key_queue_notifications(), payload)
    except Exception as exc:
        log.warning(
            "notifier.enqueue_failed",
            extra={
                "event_type": event.event_type,
                "skill_id": event.skill_id,
                "event_id": event.event_id,
                "err": str(exc),
            },
        )
        return False

    log.debug(
        "notifier.enqueued",
        extra={
            "event_type": event.event_type,
            "skill_id": event.skill_id,
            "event_id": event.event_id,
        },
    )
    return True


__all__ = ["enqueue_notification", "make_idempotency_key"]


def build_event(
    event_type: EventType,
    *,
    skill_id: str | None = None,
    contributor_email: str | None = None,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> NotificationEvent:
    """Convenience constructor — keeps producer call sites short.

    `idempotency_key=None` lets `enqueue_notification` fall back to the
    auto-derived form (good enough for events that already include
    `created_at` in their identity, like the curator weekly digest).
    Real producers should pass an explicit `make_idempotency_key(...)`
    so replays collapse correctly.
    """
    return NotificationEvent(
        event_type=event_type,
        skill_id=skill_id,
        contributor_email=contributor_email,
        payload=payload or {},
        idempotency_key=idempotency_key or "",
    )


__all__.append("build_event")
