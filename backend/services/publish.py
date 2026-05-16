"""Publish service.

The strict ordering below is the canonical example of AGENTS.md §4 rule #1.
ANY change to this file requires re-reading that rule.

1. Acquire publish lock (Redis NX EX).
2. Read Cosmos doc.
3. Build deterministic tar.gz from staged bytes.
4. Upload to Blob.
5. Cosmos write (status → approved, bundle metadata) — SOURCE OF TRUTH FLIP.
6. Audit (approve + publish).
7. Redis invalidation — LAST, only after Cosmos succeeded.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient
from redis.asyncio import Redis

from backend.core.blob import put_published
from backend.core.config import Settings
from backend.core.errors import InvalidBundle, SkillNotFound
from backend.core.logging import bind, get_logger
from backend.core.redis import key_cache_item, key_cache_list, key_lock_publish, redis_lock
from backend.models.skill import Bundle, SkillDoc
from backend.services import audit as audit_svc
from backend.services.skill_bundle import build_tar, extract_tar

log = get_logger(__name__)


async def publish(
    *,
    skill_id: str,
    actor: str,
    settings: Settings,
    skills: ContainerProxy,
    audit: ContainerProxy,
    blob: BlobServiceClient,
    redis: Redis,
) -> SkillDoc:
    """Approve + publish a pending/classified skill. Idempotent if already approved."""
    bind(skill_id=skill_id, actor=actor)

    async with redis_lock(redis, key_lock_publish(skill_id), ttl=settings.publish_lock_ttl_seconds):
        doc = await _load_latest(skills, skill_id)
        if doc is None:
            raise SkillNotFound(f"skill {skill_id!r} not found")

        # Idempotent: if already approved with a bundle, return as-is.
        if doc.status == "approved" and doc.bundle is not None:
            log.info("publish_idempotent_noop")
            return doc

        if not doc.pending_bundle_b64:
            # TODO(M1): replace pending_bundle_b64 with a staging/ Blob container.
            raise InvalidBundle("no staged bundle bytes on pending doc")

        staged_tar = base64.b64decode(doc.pending_bundle_b64.encode("ascii"))
        # Re-pack deterministically so checksums are reproducible across runs.
        files = extract_tar(staged_tar)
        tar_bytes, checksum = build_tar(files)

        blob_url = await put_published(
            blob,
            settings,
            skill_id=skill_id,
            version=doc.version,
            data=tar_bytes,
        )

        before = {"status": doc.status, "bundle": None}
        doc.status = "approved"
        doc.classifier_status = doc.classifier_status if doc.classifier_status == "done" else "done"
        doc.approved_at = datetime.now(UTC)
        doc.approver = actor
        doc.bundle = Bundle(
            blob_url=blob_url,
            checksum_sha256=checksum,
            size_bytes=len(tar_bytes),
            file_count=len(files),
        )
        doc.pending_bundle_b64 = None  # M0 shortcut — drop staged bytes post-publish.

        # 1. Cosmos write FIRST.
        await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))

        # 2. Audit (two rows — approve + publish — both reference the same actor).
        after = {"status": "approved", "version": doc.version, "checksum": checksum}
        await audit_svc.record(
            audit, skill_id=skill_id, action="approve", actor=actor, before=before, after=after
        )
        await audit_svc.record(
            audit,
            skill_id=skill_id,
            action="publish",
            actor=actor,
            after={"blob_url": blob_url, "checksum": checksum, "size_bytes": len(tar_bytes)},
        )

        # 3. Redis invalidation LAST. Failure is non-fatal (rule #2).
        try:
            await redis.delete(key_cache_list(), key_cache_item(skill_id))
        except Exception as exc:  # pragma: no cover
            log.warning("cache_invalidation_failed", extra={"err": str(exc)})

        return doc


async def reject(
    *,
    skill_id: str,
    actor: str,
    reason: str,
    skills: ContainerProxy,
    audit: ContainerProxy,
) -> SkillDoc:
    """Mark a skill rejected with a manager-provided reason."""
    bind(skill_id=skill_id, actor=actor)
    doc = await _load_latest(skills, skill_id)
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")
    before = {"status": doc.status}
    doc.status = "rejected"
    doc.rejection_reason = reason
    doc.pending_bundle_b64 = None
    await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
    await audit_svc.record(
        audit,
        skill_id=skill_id,
        action="reject",
        actor=actor,
        before=before,
        after={"status": "rejected"},
        metadata={"reason": reason},
    )
    return doc


async def _load_latest(skills: ContainerProxy, skill_id: str) -> SkillDoc | None:
    """Find the latest (non-archived) doc for a skill_id."""
    query = "SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC"
    params = [{"name": "@id", "value": skill_id}]
    items = [
        item
        async for item in skills.query_items(query=query, parameters=params, partition_key=skill_id)
    ]
    if not items:
        return None
    return SkillDoc.model_validate(items[0])
