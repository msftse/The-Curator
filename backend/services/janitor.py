"""Janitor sweep — re-queue lost classifier messages.

Implements the AGENTS.md §4 rule #4 mitigation: scans the `skills` container
for stale `classifier_status in ('queued', 'running', 'failed')` docs and
RPUSHes their `id` back onto `queue:classifier` so the classifier worker
picks them up. This self-heals uploads whose original queue message was lost
or whose classifier worker crashed mid-attempt.

Audit action is `classify` with `metadata.requeued_by='janitor'` — keeps
the existing audit query surface unchanged.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis

from backend.core.config import Settings
from backend.core.logging import bind, get_logger
from backend.core.redis import key_queue_classifier, key_queue_defender
from backend.services import audit as audit_svc

log = get_logger(__name__)


async def janitor_classifier_queue(
    *,
    skills: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    bind(actor="system:janitor")
    cutoff = now - timedelta(
        seconds=(
            settings.classifier_blpop_timeout_seconds * settings.janitor_classifier_stale_multiplier
        )
    )

    query = (
        "SELECT * FROM c WHERE c.classifier_status IN ('queued', 'running', 'failed') "
        "AND c.status IN ('pending', 'classified', 'approved') AND c.uploaded_at < @cutoff"
    )
    params = [{"name": "@cutoff", "value": cutoff.isoformat()}]

    scanned = 0
    requeued = 0
    async for raw in skills.query_items(query=query, parameters=params):
        scanned += 1
        doc_id = raw.get("id")
        skill_id = raw.get("skill_id")
        if not doc_id or not skill_id:
            continue
        try:
            await redis.rpush(key_queue_classifier(), doc_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "janitor_rpush_failed",
                extra={"doc_id": doc_id, "err": str(exc)},
            )
            continue
        with contextlib.suppress(Exception):
            await audit_svc.record(
                audit,
                skill_id=skill_id,
                action="classify",
                actor="system:janitor",
                metadata={
                    "requeued_by": "janitor",
                    "doc_id": doc_id,
                    "classifier_status": raw.get("classifier_status"),
                    "status": raw.get("status"),
                },
            )
        requeued += 1

    return {"scanned": scanned, "requeued": requeued}


async def janitor_defender_queue(
    *,
    skills: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, int]:
    """Re-queue skill docs stuck in defender (M5 follow-up — plan §3 step 5).

    Sweeps Cosmos for `defender_status in ('pending', 'failed')` whose
    `defender_scanned_at` (or `uploaded_at` when never touched) is older
    than `defender_blpop_timeout_seconds * janitor_defender_stale_multiplier`
    seconds ago. Each candidate's `id` is RPUSHed onto `queue:defender`
    so the defender worker picks it up again.

    Cosmos-first: we do NOT mutate the skill doc here — the doc is the
    source of truth and the defender worker is responsible for flipping
    `defender_status` on its next pass. We only emit a `classify`-shaped
    audit row with `actor='system:defender_janitor'` so the requeue is
    durable for compliance.
    """
    now = now or datetime.now(UTC)
    bind(actor="system:defender_janitor")
    stale_seconds = (
        settings.defender_blpop_timeout_seconds * settings.janitor_defender_stale_multiplier
    )
    cutoff = now - timedelta(seconds=stale_seconds)

    # Use COALESCE-style fallback: docs that never reached the defender
    # worker have `defender_scanned_at IS NULL` — fall back to `uploaded_at`
    # so brand-new uploads aren't immune to the sweep.
    query = (
        "SELECT * FROM c WHERE c.defender_status IN ('pending', 'failed') "
        "AND ((IS_NULL(c.defender_scanned_at) AND c.uploaded_at < @cutoff) "
        "OR (NOT IS_NULL(c.defender_scanned_at) AND c.defender_scanned_at < @cutoff))"
    )
    params = [{"name": "@cutoff", "value": cutoff.isoformat()}]

    scanned = 0
    requeued = 0
    async for raw in skills.query_items(query=query, parameters=params):
        scanned += 1
        doc_id = raw.get("id")
        skill_id = raw.get("skill_id")
        if not doc_id or not skill_id:
            continue
        try:
            await redis.rpush(key_queue_defender(), doc_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "defender_janitor_rpush_failed",
                extra={"doc_id": doc_id, "err": str(exc)},
            )
            continue
        with contextlib.suppress(Exception):
            await audit_svc.record(
                audit,
                skill_id=skill_id,
                action="classify",  # piggy-back on existing audit action; mirrors classifier janitor.
                actor="system:defender_janitor",
                metadata={
                    "requeued_by": "defender_janitor",
                    "doc_id": doc_id,
                    "defender_status": raw.get("defender_status"),
                },
            )
        requeued += 1

    return {"scanned": scanned, "requeued": requeued}
