"""Admin-triggered Defender rescan."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis

from backend.core.errors import InvalidStatusTransition, SkillNotFound
from backend.core.redis import key_cache_item, key_queue_defender
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc
from backend.services.cosmos_helpers import replace_with_etag_retry


async def requeue_defender(
    *,
    skill_id: str,
    actor: str,
    actor_oid: str | None = None,
    skills: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
    now: datetime | None = None,
) -> SkillDoc:
    now = now or datetime.now(UTC)
    doc = await _load_latest(skills, skill_id)
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")
    if doc.status == "quarantined":
        raise InvalidStatusTransition(
            f"skill {skill_id!r} is quarantined and cannot be rescanned",
            metadata={"status": doc.status, "defender_status": doc.defender_status},
        )

    before = {
        "status": doc.status,
        "defender_status": doc.defender_status,
        "defender_severity": doc.defender_severity,
    }

    def _mark_pending(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        d.defender_status = "pending"
        d.defender_severity = None
        d.defender_report = None
        d.defender_scanned_at = None
        return d.model_dump(mode="json")

    updated_raw = await replace_with_etag_retry(
        skills,
        item_id=doc.id,
        partition_key=doc.skill_id,
        mutate=_mark_pending,
    )
    updated = SkillDoc.model_validate(updated_raw)

    await audit_svc.record(
        audit,
        skill_id=skill_id,
        action="classify",
        actor=actor,
        actor_oid=actor_oid,
        before=before,
        after={"defender_status": "pending"},
        metadata={"phase": "defender", "source": "admin_rescan", "requeued_at": now.isoformat()},
    )

    await redis.rpush(key_queue_defender(), doc.id)
    with contextlib.suppress(Exception):
        await redis.delete(key_cache_item(skill_id))
    return updated


async def _load_latest(skills: ContainerProxy, skill_id: str) -> SkillDoc | None:
    rows: list[dict[str, Any]] = []
    async for raw in skills.query_items(
        query="SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC",
        parameters=[{"name": "@id", "value": skill_id}],
        partition_key=skill_id,
    ):
        rows.append(raw)
        break
    if not rows:
        return None
    return SkillDoc.model_validate(rows[0])
