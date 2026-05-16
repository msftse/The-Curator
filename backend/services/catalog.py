"""Catalog read service — Redis cache with Cosmos fallback (AGENTS.md §4 rule #2)."""

from __future__ import annotations

import contextlib
import json
import logging

from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.core.config import Settings
from backend.core.redis import key_cache_item, key_cache_list
from backend.models.api import SkillListItem
from backend.models.skill import SkillDoc

log = logging.getLogger(__name__)


def _to_list_item(doc: SkillDoc) -> SkillListItem:
    return SkillListItem(
        skill_id=doc.skill_id,
        version=doc.version,
        name=doc.name,
        description=doc.description,
        status=doc.status,
        classifier_status=doc.classifier_status,
        uploader=doc.uploader,
        uploaded_at=doc.uploaded_at,
        approved_at=doc.approved_at,
        classification=doc.classification,
        bundle=doc.bundle,
        pinned=doc.pinned,
        user_category=doc.user_category,
        user_tags=list(doc.user_tags),
    )


async def list_approved(
    *,
    skills: ContainerProxy,
    redis: Redis,
    settings: Settings,
) -> list[SkillListItem]:
    """Return all approved skills. Cache-on-read, Cosmos fallback."""
    cache_key = key_cache_list()
    try:
        cached = await redis.get(cache_key)
        if cached:
            return [SkillListItem.model_validate(x) for x in json.loads(cached)]
    except RedisError as exc:
        log.warning("redis_unavailable_fallback_to_cosmos", extra={"err": str(exc)})

    query = "SELECT * FROM c WHERE c.status='approved'"
    items: list[SkillListItem] = []
    async for raw in skills.query_items(query=query):
        items.append(_to_list_item(SkillDoc.model_validate(raw)))

    try:
        await redis.set(
            cache_key,
            json.dumps([i.model_dump(mode="json") for i in items]),
            ex=settings.cache_list_ttl_seconds,
        )
    except RedisError:
        pass  # cache write failure is non-fatal
    return items


async def get_skill(
    *,
    skill_id: str,
    skills: ContainerProxy,
    redis: Redis,
    settings: Settings,
) -> SkillDoc | None:
    """Latest doc for a skill_id, with cache + Cosmos fallback."""
    cache_key = key_cache_item(skill_id)
    try:
        cached = await redis.get(cache_key)
        if cached:
            return SkillDoc.model_validate(json.loads(cached))
    except RedisError as exc:
        log.warning("redis_unavailable_fallback_to_cosmos", extra={"err": str(exc)})

    query = "SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC"
    params = [{"name": "@id", "value": skill_id}]
    rows = [
        r async for r in skills.query_items(query=query, parameters=params, partition_key=skill_id)
    ]
    if not rows:
        return None
    doc = SkillDoc.model_validate(rows[0])

    with contextlib.suppress(RedisError):
        await redis.set(
            cache_key,
            json.dumps(doc.model_dump(mode="json")),
            ex=settings.cache_item_ttl_seconds,
        )
    return doc


async def list_my_submissions(
    *,
    uploader: str,
    skills: ContainerProxy,
) -> list[SkillListItem]:
    """Submissions for a single contributor — no cache (small per-user view)."""
    query = "SELECT * FROM c WHERE c.uploader=@u ORDER BY c.uploaded_at DESC"
    params = [{"name": "@u", "value": uploader}]
    items: list[SkillListItem] = []
    async for raw in skills.query_items(query=query, parameters=params):
        items.append(_to_list_item(SkillDoc.model_validate(raw)))
    return items


async def list_review_queue(
    *,
    skills: ContainerProxy,
) -> list[SkillListItem]:
    """Manager view — pending + classified skills awaiting review."""
    query = "SELECT * FROM c WHERE c.status IN ('pending','classified') ORDER BY c.uploaded_at ASC"
    items: list[SkillListItem] = []
    async for raw in skills.query_items(query=query):
        items.append(_to_list_item(SkillDoc.model_validate(raw)))
    return items
