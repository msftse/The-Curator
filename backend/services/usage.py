"""Usage ingestion service.

Ordering per AGENTS.md §4 rule #1:
1. Write raw `usage_events` row (Cosmos — TTL handles eviction).
2. Bump aggregated counters on the SkillDoc with optimistic concurrency
   (`if_match=etag`, retry up to 3x on 412) — SOURCE OF TRUTH FLIP.
3. Invalidate Redis cache keys (`cache:skills:list:v1`,
   `cache:skills:item:{id}`) — LAST, failures non-fatal (rule #2).

If the skill is `archived` we still record the raw event (operators may want
the trail) but skip the counter update + cache invalidate — archived skills
don't appear in the cached catalog list.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.core.config import Settings
from backend.core.logging import bind
from backend.core.redis import key_cache_item, key_cache_list
from backend.models.curator import UsageEventDoc
from backend.models.skill import SkillDoc
from backend.services.cosmos_helpers import replace_with_etag_retry

log = logging.getLogger(__name__)


async def record_usage_event(
    *,
    skill_id: str,
    version: str,
    loader_id: str,
    context: dict[str, Any],
    skills: ContainerProxy,
    usage: ContainerProxy,
    redis: Redis,
    settings: Settings,
) -> UsageEventDoc:
    bind(skill_id=skill_id, actor=f"loader:{loader_id}")
    now = datetime.now(UTC)

    # 1. Raw event row first. Failure here is fatal — caller returns 503.
    event = UsageEventDoc(
        skill_id=skill_id,
        version=version,
        loader_id=loader_id,
        at=now,
        context=context,
    )
    await usage.create_item(body=event.model_dump(mode="json"))

    # 2. Find the latest doc (we need its `id` for the partition write).
    latest = await _load_latest_id(skills, skill_id)
    if latest is None:
        # Skill vanished between catalog check and counter bump — rare. Skip.
        return event

    doc_id, current_status = latest
    if current_status == "archived":
        # Don't bump counters for archived skills; raw row already persisted.
        return event

    loaders_30d = await recompute_loaders_30d(
        usage,
        skill_id,
        now,
        window_days=settings.usage_loaders_30d_window_days,
    )

    def _bump(body: dict[str, Any]) -> dict[str, Any]:
        doc = SkillDoc.model_validate(body)
        doc.usage.load_count += 1
        doc.usage.last_loaded_at = now
        doc.usage.loaders_30d = loaders_30d
        return doc.model_dump(mode="json")

    await replace_with_etag_retry(
        skills,
        item_id=doc_id,
        partition_key=skill_id,
        mutate=_bump,
    )

    # 3. Redis invalidation LAST. Non-fatal.
    with contextlib.suppress(RedisError, Exception):
        await redis.delete(key_cache_list(), key_cache_item(skill_id))

    return event


async def recompute_loaders_30d(
    usage: ContainerProxy,
    skill_id: str,
    now: datetime,
    *,
    window_days: int = 30,
) -> int:
    """Count distinct loader_ids for a skill within the last `window_days`."""
    cutoff = now - timedelta(days=window_days)
    seen: set[str] = set()
    query = (
        "SELECT c.loader_id FROM c "
        "WHERE c.skill_id=@id AND c.at >= @cutoff"
    )
    params = [
        {"name": "@id", "value": skill_id},
        {"name": "@cutoff", "value": cutoff.isoformat()},
    ]
    async for row in usage.query_items(
        query=query, parameters=params, partition_key=skill_id
    ):
        lid = row.get("loader_id")
        if lid:
            seen.add(lid)
    return len(seen)


async def _load_latest_id(
    skills: ContainerProxy, skill_id: str
) -> tuple[str, str] | None:
    query = "SELECT c.id, c.status FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC"
    params = [{"name": "@id", "value": skill_id}]
    async for row in skills.query_items(
        query=query, parameters=params, partition_key=skill_id
    ):
        return row["id"], row.get("status", "pending")
    return None
