"""Janitor sweep — re-queue lost classifier messages.

Implements the AGENTS.md §4 rule #4 mitigation: scans the `skills` container
for `classifier_status='queued'` docs older than a stale-threshold and
RPUSHes their `id` back onto `queue:classifier` so the classifier worker
picks them up.

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
from backend.core.redis import key_queue_classifier
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
            settings.classifier_blpop_timeout_seconds
            * settings.janitor_classifier_stale_multiplier
        )
    )

    query = (
        "SELECT * FROM c "
        "WHERE c.classifier_status='queued' AND c.uploaded_at < @cutoff"
    )
    params = [{"name": "@cutoff", "value": cutoff.isoformat()}]

    scanned = 0
    requeued = 0
    async for raw in skills.query_items(
        query=query, parameters=params, enable_cross_partition_query=True
    ):
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
                metadata={"requeued_by": "janitor", "doc_id": doc_id},
            )
        requeued += 1

    return {"scanned": scanned, "requeued": requeued}
