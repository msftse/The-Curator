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
from backend.core.errors import (
    InvalidBundle,
    InvalidStatusTransition,
    JustificationRequired,
    SkillNotFound,
)
from backend.core.logging import bind, get_logger
from backend.core.redis import key_cache_item, key_cache_list, key_lock_publish, redis_lock
from backend.models.defender import severity_behavior
from backend.models.skill import Bundle, SkillDoc
from backend.services import audit as audit_svc
from backend.services.notifier import (
    build_event,
    enqueue_notification,
    make_idempotency_key,
)
from backend.services.skill_bundle import build_tar, extract_tar

log = get_logger(__name__)


async def publish(
    *,
    skill_id: str,
    actor: str,
    actor_oid: str | None = None,
    settings: Settings,
    skills: ContainerProxy,
    audit: ContainerProxy,
    blob: BlobServiceClient,
    redis: Redis,
    defender_override: bool = False,
    defender_justification: str | None = None,
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

        override_justification = _validate_defender_approval(
            doc,
            defender_override=defender_override,
            defender_justification=defender_justification,
            settings=settings,
        )

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
        defender_before = None
        if override_justification is not None:
            defender_before = {
                "defender_status": doc.defender_status,
                "defender_severity": doc.defender_severity,
            }
            doc.defender_status = "clean"
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
        if override_justification is not None:
            await audit_svc.record(
                audit,
                skill_id=skill_id,
                action="defender_override",
                actor=actor,
                actor_oid=actor_oid,
                before=defender_before,
                after={
                    "defender_status": "clean",
                    "defender_severity": doc.defender_severity,
                },
                metadata={
                    "justification": override_justification,
                    "version": doc.version,
                    "defender_severity": doc.defender_severity,
                    "defender_report_id": doc.defender_report_id,
                    "source": "approve_inline",
                },
            )
        await audit_svc.record(
            audit,
            skill_id=skill_id,
            action="approve",
            actor=actor,
            actor_oid=actor_oid,
            before=before,
            after=after,
            metadata=(
                {
                    "defender_override": True,
                    "defender_severity": doc.defender_severity,
                }
                if override_justification is not None
                else None
            ),
        )
        await audit_svc.record(
            audit,
            skill_id=skill_id,
            action="publish",
            actor=actor,
            actor_oid=actor_oid,
            after={"blob_url": blob_url, "checksum": checksum, "size_bytes": len(tar_bytes)},
        )

        # 3. Redis invalidation LAST. Failure is non-fatal (rule #2).
        try:
            await redis.delete(key_cache_list(), key_cache_item(skill_id))
        except Exception as exc:  # pragma: no cover
            log.warning("cache_invalidation_failed", extra={"err": str(exc)})

        # 4. Notifier producer — `skill.approved` to the contributor.
        #    Fire-and-forget; Cosmos write above is the source of truth.
        if override_justification is not None:
            await enqueue_notification(
                build_event(
                    "admin.override",
                    skill_id=skill_id,
                    payload={
                        "skill_id": skill_id,
                        "version": doc.version,
                        "name": doc.name,
                        "overridden_by": actor,
                        "justification": override_justification,
                        "defender_severity": doc.defender_severity,
                    },
                    idempotency_key=make_idempotency_key(
                        "admin.override",
                        skill_id=skill_id,
                        version=doc.version,
                        extra=doc.id,
                    ),
                ),
                redis=redis,
            )
        await enqueue_notification(
            build_event(
                "skill.approved",
                skill_id=skill_id,
                contributor_email=doc.uploader,
                payload={
                    "skill_id": skill_id,
                    "version": doc.version,
                    "name": doc.name,
                    "approver": actor,
                    "checksum": checksum,
                },
                idempotency_key=make_idempotency_key(
                    "skill.approved",
                    skill_id=skill_id,
                    version=doc.version,
                    extra=doc.id,
                ),
            ),
            redis=redis,
        )

        return doc


def _validate_defender_approval(
    doc: SkillDoc,
    *,
    defender_override: bool,
    defender_justification: str | None,
    settings: Settings,
) -> str | None:
    """Return trimmed override justification when required, else None.

    Low-severity defender findings are warnings and may be approved normally.
    Medium/high/critical findings need an explicit admin override with a
    justification before any bundle bytes are published.
    """
    if doc.defender_status != "flagged":
        if doc.defender_status != "clean":
            raise InvalidStatusTransition(
                "skill cannot be approved until defender scan completes cleanly "
                "or a flagged finding is overridden",
                metadata={
                    "defender_status": doc.defender_status,
                    "defender_severity": doc.defender_severity,
                },
            )
        return None

    behavior = severity_behavior(doc.defender_severity or "critical")
    if behavior == "ok":
        return None

    justification = (defender_justification or "").strip()
    min_chars = settings.quarantine_min_justification_chars
    if not defender_override or len(justification) < min_chars:
        raise JustificationRequired(
            "approving a defender-flagged skill at severity "
            f"{doc.defender_severity or 'unknown'} requires defender_override=true "
            f"and a justification of at least {min_chars} characters",
            metadata={
                "defender_status": doc.defender_status,
                "defender_severity": doc.defender_severity,
                "required_behavior": behavior,
                "min_chars": min_chars,
                "got_chars": len(justification),
            },
        )
    return justification


async def reject(
    *,
    skill_id: str,
    actor: str,
    actor_oid: str | None = None,
    reason: str,
    skills: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis | None = None,
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
        actor_oid=actor_oid,
        before=before,
        after={"status": "rejected"},
        metadata={"reason": reason},
    )

    # Notifier producer — `skill.rejected` to the contributor.
    # `redis` is optional so older test call sites that pre-date M5-6
    # continue to work without a fake Redis.
    if redis is not None:
        await enqueue_notification(
            build_event(
                "skill.rejected",
                skill_id=skill_id,
                contributor_email=doc.uploader,
                payload={
                    "skill_id": skill_id,
                    "version": doc.version,
                    "name": doc.name,
                    "reason": reason,
                    "rejector": actor,
                },
                idempotency_key=make_idempotency_key(
                    "skill.rejected",
                    skill_id=skill_id,
                    version=doc.version,
                    extra=doc.id,
                ),
            ),
            redis=redis,
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
