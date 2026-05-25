"""Curator schedule storage (M5-7).

Cosmos `system_state` is the source of truth (AGENTS.md §4 rule 1). The
reconciler worker reads from here and patches the live K8s CronJob to
match. There is intentionally no Redis cache — schedule reads happen at
most a handful of times per minute (admin page + reconciler poll) so the
extra invalidation surface isn't worth it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from azure.cosmos import exceptions as cosmos_exc
from azure.cosmos.aio import ContainerProxy

from backend.models.schedule import (
    DEFAULT_CRON,
    DEFAULT_TIMEZONE,
    CuratorSchedule,
)
from backend.services import audit as audit_svc

_DOC_ID = "curator_schedule"
_DOC_KEY = "curator_schedule"
_RESERVED_SKILL_ID = "_system"


def _now() -> datetime:
    return datetime.now(UTC)


async def get_schedule(*, system_state: ContainerProxy) -> CuratorSchedule:
    """Return the current schedule, or the default if no doc exists yet."""
    try:
        raw = await system_state.read_item(item=_DOC_ID, partition_key=_DOC_KEY)
    except cosmos_exc.CosmosResourceNotFoundError:
        return CuratorSchedule(cron=DEFAULT_CRON, timezone=DEFAULT_TIMEZONE, enabled=True)
    # Strip Cosmos metadata before pydantic validation.
    raw = {k: v for k, v in raw.items() if not k.startswith("_") and k not in ("id", "key")}
    return CuratorSchedule.model_validate(raw)


async def put_schedule(
    *,
    system_state: ContainerProxy,
    audit: ContainerProxy,
    actor: str,
    actor_oid: str | None,
    cron: str,
    timezone: str,
    enabled: bool,
) -> CuratorSchedule:
    """Upsert the schedule doc + write an audit row.

    Cron syntax has already been validated by the Pydantic model on the
    request body; we re-validate inside `CuratorSchedule` as defense in
    depth. The audit row carries the previous value so a viewer can see
    the diff without consulting Cosmos history.
    """
    before = await get_schedule(system_state=system_state)
    schedule = CuratorSchedule(
        cron=cron,
        timezone=timezone,
        enabled=enabled,
        updated_by=actor,
        updated_at=_now(),
    )
    body = schedule.model_dump(mode="json")
    body["id"] = _DOC_ID
    body["key"] = _DOC_KEY
    # Cosmos-first write (AGENTS.md §4 rule 1).
    await system_state.upsert_item(body=body)
    await audit_svc.record(
        audit,
        skill_id=_RESERVED_SKILL_ID,
        action="curator_schedule_update",
        actor=actor,
        actor_oid=actor_oid,
        before={
            "cron": before.cron,
            "timezone": before.timezone,
            "enabled": before.enabled,
        },
        after={
            "cron": schedule.cron,
            "timezone": schedule.timezone,
            "enabled": schedule.enabled,
        },
    )
    return schedule
