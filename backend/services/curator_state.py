"""Curator pause/resume state.

Truth lives in the `system_state` Cosmos container (`key="curator_pause"`).
Redis holds a hot-path cache (`curator:paused`) with no TTL — operator
intent persists. Every read tries Redis first and falls back to Cosmos on
miss/error (AGENTS.md §4 rule #2).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

from azure.cosmos import exceptions as cosmos_exc
from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.core.logging import bind
from backend.core.redis import key_curator_pause
from backend.services import audit as audit_svc

_DOC_ID = "curator_pause"
_DOC_KEY = "curator_pause"
_RESERVED_SKILL_ID = "_system"


def _now() -> datetime:
    return datetime.now(UTC)


async def pause(
    *,
    system_state: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
    actor: str,
    actor_oid: str | None = None,
) -> None:
    bind(actor=actor)
    body = {
        "id": _DOC_ID,
        "key": _DOC_KEY,
        "paused": True,
        "paused_by": actor,
        "paused_at": _now().isoformat(),
    }
    await system_state.upsert_item(body=body)
    with contextlib.suppress(RedisError, Exception):
        await redis.set(key_curator_pause(), "1")
    await audit_svc.record(
        audit,
        skill_id=_RESERVED_SKILL_ID,
        action="pause",
        actor=actor,
        actor_oid=actor_oid,
        after={"paused": True},
    )


async def resume(
    *,
    system_state: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
    actor: str,
    actor_oid: str | None = None,
) -> None:
    bind(actor=actor)
    body = {
        "id": _DOC_ID,
        "key": _DOC_KEY,
        "paused": False,
        "paused_by": actor,
        "paused_at": _now().isoformat(),
    }
    await system_state.upsert_item(body=body)
    with contextlib.suppress(RedisError, Exception):
        await redis.delete(key_curator_pause())
    await audit_svc.record(
        audit,
        skill_id=_RESERVED_SKILL_ID,
        action="resume",
        actor=actor,
        actor_oid=actor_oid,
        after={"paused": False},
    )


async def is_paused(
    *,
    system_state: ContainerProxy,
    redis: Redis,
) -> bool:
    # Redis hot-path
    try:
        val = await redis.get(key_curator_pause())
        if val is not None:
            return val in ("1", b"1", "true", "True")
    except RedisError:
        pass
    # Cosmos fallback
    try:
        raw = await system_state.read_item(item=_DOC_ID, partition_key=_DOC_KEY)
        paused = bool(raw.get("paused", False))
        if paused:
            with contextlib.suppress(RedisError, Exception):
                await redis.set(key_curator_pause(), "1")
        return paused
    except cosmos_exc.CosmosResourceNotFoundError:
        return False
    except Exception:
        return False
