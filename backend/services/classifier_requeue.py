"""Admin-triggered classifier requeue.

Used when a submission is still unclassified (or previously failed) and an
operator wants to ask the classifier worker to try again immediately instead
of waiting for the periodic janitor sweep.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis

from backend.core.errors import InvalidStatusTransition, SkillNotFound
from backend.core.redis import key_cache_item, key_queue_classifier
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc
from backend.services.cosmos_helpers import replace_with_etag_retry


async def requeue_classifier(
    *,
    skill_id: str,
    actor: str,
    actor_oid: str | None = None,
    skills: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
    now: datetime | None = None,
) -> SkillDoc:
    """Reset classifier state to queued and push doc id to queue:classifier."""
    now = now or datetime.now(UTC)
    doc = await _load_latest(skills, skill_id)
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")

    if doc.status not in {"pending", "classified", "approved"}:
        raise InvalidStatusTransition(
            f"skill {skill_id!r} with status={doc.status!r} cannot be reclassified",
            metadata={"status": doc.status, "classifier_status": doc.classifier_status},
        )

    before = {
        "status": doc.status,
        "classifier_status": doc.classifier_status,
        "classification": doc.classification.model_dump(mode="json")
        if doc.classification
        else None,
    }

    def _mark_queued(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        d.classifier_status = "queued"
        if d.status == "classified" and d.classification is None:
            d.status = "pending"
        return d.model_dump(mode="json")

    updated_raw = await replace_with_etag_retry(
        skills,
        item_id=doc.id,
        partition_key=doc.skill_id,
        mutate=_mark_queued,
    )
    updated = SkillDoc.model_validate(updated_raw)

    await audit_svc.record(
        audit,
        skill_id=skill_id,
        action="classify",
        actor=actor,
        actor_oid=actor_oid,
        before=before,
        after={"classifier_status": "queued"},
        metadata={
            "source": "admin_requeue",
            "doc_id": doc.id,
            "requeued_at": now.isoformat(),
        },
    )

    await redis.rpush(key_queue_classifier(), doc.id)
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
