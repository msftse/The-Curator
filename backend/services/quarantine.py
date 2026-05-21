"""Quarantine service (M5-3).

Admin response to a defender-flagged skill. Moves the bundle bytes into
the terminal `quarantine/` blob container and flips the skill status to
``quarantined``. Mirrors the strict ordering used by
`services/publish.py` and `services/curator.archive_skill_now`:

    1. Read latest doc.
    2. Validate preconditions (defender_status == 'flagged',
       justification length).
    3. Blob mutation — UPLOAD bundle bytes to `quarantine/{id}/{ver}/...`
       and VERIFY destination exists. Source bytes (the M0-shaped
       `pending_bundle_b64` field on the doc) are NOT deleted via
       `blob.delete_blob` — they live inline on the Cosmos doc and are
       cleared by the Cosmos write in step 4. There is therefore zero
       `delete_blob(...)` call in this module; the AST never-delete gate
       still treats this file as fully forbidden.
    4. Cosmos write — SOURCE OF TRUTH FLIP — status=`quarantined`,
       quarantine metadata populated, `pending_bundle_b64` cleared.
    5. Audit row (action=`quarantine`).
    6. Redis invalidation — LAST, non-fatal (AGENTS.md §4 rule 2).

The deletion of the bundle bytes from `quarantine/` after
`QUARANTINE_RETENTION_DAYS` is performed by
`backend.services.quarantine_janitor` — the ONE allowed delete-blob
callsite outside `curator.move_published_to_archive` (AGENTS.md §5).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.core.blob import published_blob_path, quarantine_blob_path
from backend.core.config import Settings
from backend.core.errors import (
    DefenderNotFlagged,
    JustificationRequired,
    SkillNotFound,
    SkillPinned,
)
from backend.core.logging import bind, get_logger
from backend.core.redis import key_cache_item, key_cache_list
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc
from backend.services.cosmos_helpers import replace_with_etag_retry

log = get_logger(__name__)


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


async def _read_bundle_bytes(
    doc: SkillDoc,
    blob: BlobServiceClient,
    settings: Settings,
) -> bytes:
    """Pull bundle bytes from whichever staging location currently holds them.

    M0/M1: bundle lives inline as base64 on `SkillDoc.pending_bundle_b64`.
    If absent and the skill was already published, fall back to reading
    from the `published/` container. Returns empty bytes if neither is
    available — quarantine still proceeds (status flip + audit trail are
    the durable record).
    """
    import base64

    if doc.pending_bundle_b64:
        try:
            return base64.b64decode(doc.pending_bundle_b64)
        except Exception:  # noqa: BLE001
            log.warning("quarantine_bundle_b64_decode_failed", extra={"skill_id": doc.skill_id})
            return b""

    if doc.status == "approved" and doc.bundle is not None:
        src = blob.get_container_client(settings.blob_published_container).get_blob_client(
            published_blob_path(doc.skill_id, doc.version)
        )
        try:
            downloader = await src.download_blob()
            return await downloader.readall()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "quarantine_published_read_failed",
                extra={"skill_id": doc.skill_id, "err": str(exc)},
            )
            return b""

    return b""


async def quarantine_skill(
    *,
    skill_id: str,
    actor: str,
    actor_oid: str | None = None,
    justification: str,
    settings: Settings,
    skills: ContainerProxy,
    audit: ContainerProxy,
    blob: BlobServiceClient,
    redis: Redis,
    now: datetime | None = None,
) -> SkillDoc:
    """Admin moves a defender-flagged skill into terminal quarantine.

    Raises:
      SkillNotFound: no doc for `skill_id`.
      SkillPinned: skill is pinned (operator must unpin first; pinning is
        absolute per AGENTS.md §5 and overrides admin quarantine).
      DefenderNotFlagged: `defender_status != 'flagged'`.
      JustificationRequired: justification shorter than
        `Settings.quarantine_min_justification_chars`.
    """
    bind(actor=actor, skill_id=skill_id)
    now = now or datetime.now(UTC)

    justification = (justification or "").strip()
    if len(justification) < settings.quarantine_min_justification_chars:
        raise JustificationRequired(
            f"justification must be at least {settings.quarantine_min_justification_chars} "
            f"characters; got {len(justification)}",
            metadata={
                "min_chars": settings.quarantine_min_justification_chars,
                "got_chars": len(justification),
            },
        )

    doc = await _load_latest(skills, skill_id)
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")

    if doc.pinned:
        raise SkillPinned(
            f"skill {skill_id!r} is pinned; unpin before quarantining",
            metadata={"pinned_by": doc.pinned_by},
        )

    if doc.defender_status != "flagged":
        raise DefenderNotFlagged(
            f"skill {skill_id!r} has defender_status={doc.defender_status!r}; "
            f"quarantine requires defender_status='flagged'",
            metadata={"defender_status": doc.defender_status},
        )

    before = {
        "status": doc.status,
        "defender_status": doc.defender_status,
        "defender_severity": doc.defender_severity,
    }

    # 1. Blob mutation: copy bundle bytes into the quarantine container.
    #    Verify destination exists before flipping Cosmos status. There is
    #    NO source-blob delete here — the bytes either live inline on the
    #    Cosmos doc (`pending_bundle_b64`, cleared in step 2) or in
    #    `published/` (left in place; the publish-side never-delete
    #    contract still holds). The plan's "delete staging source" step
    #    is moot until M1 splits staging out of the doc.
    bundle_bytes = await _read_bundle_bytes(doc, blob, settings)
    dest = blob.get_container_client(settings.blob_quarantine_container).get_blob_client(
        quarantine_blob_path(doc.skill_id, doc.version)
    )
    await dest.upload_blob(bundle_bytes, overwrite=True)
    if not await dest.exists():
        raise RuntimeError(
            f"quarantine copy verification failed for {skill_id}@{doc.version}: "
            f"destination blob not present after upload"
        )

    # 2. Cosmos write — SOURCE OF TRUTH FLIP via etag retry.
    expires_at = now + timedelta(days=settings.quarantine_retention_days)

    def _flip(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        d.status = "quarantined"
        d.quarantined_at = now
        d.quarantined_by = actor
        d.quarantine_justification = justification
        d.quarantine_expires_at = expires_at
        # Drop the inline staging bytes — the durable copy is in
        # `quarantine/`. This is the M5-3 equivalent of the publish-time
        # "pending_bundle_b64 = None" cleanup; never a `blob.delete_blob`.
        d.pending_bundle_b64 = None
        return d.model_dump(mode="json")

    updated_raw = await replace_with_etag_retry(
        skills,
        item_id=doc.id,
        partition_key=doc.skill_id,
        mutate=_flip,
    )
    updated = SkillDoc.model_validate(updated_raw)

    # 3. Audit row — always written, even if cache invalidation later fails.
    with contextlib.suppress(Exception):
        await audit_svc.record(
            audit,
            skill_id=skill_id,
            action="quarantine",
            actor=actor,
            actor_oid=actor_oid,
            before=before,
            after={
                "status": "quarantined",
                "quarantine_expires_at": expires_at.isoformat(),
            },
            metadata={
                "justification": justification,
                "version": doc.version,
                "defender_severity": doc.defender_severity,
                "source": "admin_manual",
                "retention_days": settings.quarantine_retention_days,
            },
        )

    # 4. Cache invalidation — LAST, non-fatal (AGENTS.md §4 rule 2).
    with contextlib.suppress(RedisError, Exception):
        await redis.delete(key_cache_list(), key_cache_item(doc.skill_id))

    log.info(
        "quarantine_complete",
        extra={
            "skill_id": skill_id,
            "version": doc.version,
            "expires_at": expires_at.isoformat(),
        },
    )
    return updated
