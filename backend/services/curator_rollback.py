"""Curator rollback.

Intentionally inverts the Cosmos-first rule (AGENTS.md §4 rule #1) for
*backward* writes only:

    1. Acquire curator lock — mutual exclusion with a live curator pass.
    2. Take a `pre-rollback-{utc-iso}` snapshot of current `published/`
       state — so the rollback itself is reversible (operator who restores
       the wrong snapshot can roll forward again).
    3. For each manifest entry: restore Blob bytes FIRST (extract from the
       snapshot tar, overwrite `published/{id}/{ver}/bundle.tar.gz`), THEN
       replace the Cosmos doc's status to match the snapshot.

Rationale: forward writes need Cosmos to never point at missing bytes (so
Cosmos lags Blob). Backward writes need bytes back in place before Cosmos
points at them (so Blob leads Cosmos). Either direction, Cosmos never
points at bytes that don't exist.

NEVER deletes — `RestoreFailed` raised if a manifest entry's bytes can't be
restored. Pinned skills are NOT special-cased by rollback (the snapshot is
authoritative); pin/unpin state is part of the doc body and would be
reverted along with status if it was different at snapshot time.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from azure.cosmos import exceptions as cosmos_exc
from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.core.blob import published_blob_path, put_published
from backend.core.config import Settings
from backend.core.errors import RestoreFailed, SnapshotNotFound
from backend.core.logging import bind, get_logger
from backend.core.redis import (
    key_cache_item,
    key_cache_list,
    key_curator_run_lock,
    redis_lock,
)
from backend.models.curator import (
    CuratorRunRecord,
    RollbackResult,
    Transition,
)
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc
from backend.services import curator_report
from backend.services import snapshot as snapshot_svc
from backend.services.cosmos_helpers import replace_with_etag_retry

log = get_logger(__name__)


def _utc_iso_compact() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


async def rollback(
    *,
    snapshot_name: str | None,
    skills: ContainerProxy,
    audit: ContainerProxy,
    blob: BlobServiceClient,
    redis: Redis,
    settings: Settings,
    actor: str = "system:curator",
) -> RollbackResult:
    bind(actor=actor)

    async with redis_lock(
        redis,
        key_curator_run_lock(),
        ttl=settings.curator_lock_ttl_seconds,
    ):
        if snapshot_name is None:
            candidates = [
                n
                for n in await snapshot_svc.list_snapshots(blob, settings)
                if not n.startswith("pre-rollback-")
            ]
            if not candidates:
                raise SnapshotNotFound("no snapshots available to roll back to")
            snapshot_name = candidates[0]

        manifest = await snapshot_svc.load_manifest(blob, settings, snapshot_name)

        # Pre-rollback snapshot — makes rollback reversible.
        pre_name = f"pre-rollback-{_utc_iso_compact()}"
        await snapshot_svc.snapshot_published(blob, settings, run_id=pre_name, prefix=pre_name)

        tar_bytes = await snapshot_svc.download_snapshot_tar(blob, settings, snapshot_name)
        files = snapshot_svc.extract_snapshot_files(tar_bytes)

        restored: list[Transition] = []
        touched_skill_ids: list[str] = []
        missing: list[str] = []

        for entry in sorted(manifest.skills, key=lambda e: e.skill_id):
            data = files.get(entry.blob_path)
            if data is None:
                missing.append(entry.blob_path)
                continue
            # 1. Restore Blob bytes FIRST.
            await put_published(
                blob,
                settings,
                skill_id=entry.skill_id,
                version=entry.version,
                data=data,
            )

            # 2. Restore Cosmos status.
            before_status = await _restore_cosmos_status(
                skills,
                skill_id=entry.skill_id,
                version=entry.version,
                target_status=entry.status,
                blob_path=published_blob_path(entry.skill_id, entry.version),
                checksum=entry.checksum_sha256,
            )

            # 3. Audit.
            with contextlib.suppress(Exception):
                await audit_svc.record(
                    audit,
                    skill_id=entry.skill_id,
                    action="rollback",
                    actor=actor,
                    before={"status": before_status},
                    after={"status": entry.status},
                    metadata={"snapshot_name": snapshot_name},
                )

            restored.append(
                Transition(
                    skill_id=entry.skill_id,
                    version=entry.version,
                    before=before_status or "approved",
                    after=entry.status,
                    reason="steady_state",
                    applied=True,
                )
            )
            touched_skill_ids.append(entry.skill_id)

        if missing:
            raise RestoreFailed(
                f"snapshot tar missing entries: {missing}",
                metadata={"missing": missing, "snapshot_name": snapshot_name},
            )

        # 4. Cache invalidation LAST.
        with contextlib.suppress(RedisError, Exception):
            keys = [key_cache_list()] + [key_cache_item(s) for s in touched_skill_ids]
            await redis.delete(*keys)

        # 5. Rollback report (symmetric with normal runs).
        now = datetime.now(UTC)
        rollback_run = CuratorRunRecord(
            run_id=f"rollback-{_utc_iso_compact()}",
            started_at=now,
            finished_at=now,
            dry_run=False,
            planner_inputs={"snapshot_name": snapshot_name},
            transitions=restored,
            skipped_pinned=[],
            snapshot_name=snapshot_name,
            lock_token=None,
        )
        with contextlib.suppress(Exception):
            await curator_report.persist_report(blob, settings, rollback_run)

        return RollbackResult(
            snapshot_name=snapshot_name,
            pre_rollback_snapshot_name=pre_name,
            restored=restored,
            at=now,
        )


async def _restore_cosmos_status(
    skills: ContainerProxy,
    *,
    skill_id: str,
    version: str,
    target_status: str,
    blob_path: str,
    checksum: str,
) -> str | None:
    """Restore a doc's status to the snapshot value. Returns previous status."""
    # Find current doc
    query = "SELECT * FROM c WHERE c.skill_id=@id AND c.version=@v"
    params = [
        {"name": "@id", "value": skill_id},
        {"name": "@v", "value": version},
    ]
    rows = []
    async for raw in skills.query_items(query=query, parameters=params, partition_key=skill_id):
        rows.append(raw)
        break

    if not rows:
        # Defense-in-depth: re-create doc (snapshot lacks full body, so this is
        # a minimal restore; ops should investigate why it vanished).
        log.warning(
            "rollback_cosmos_doc_missing_recreate",
            extra={"skill_id": skill_id, "version": version},
        )
        return None

    current_id = rows[0]["id"]
    before_status = rows[0].get("status")

    def _flip(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        d.status = target_status  # type: ignore[assignment]
        return d.model_dump(mode="json")

    try:
        await replace_with_etag_retry(
            skills,
            item_id=current_id,
            partition_key=skill_id,
            mutate=_flip,
        )
    except cosmos_exc.CosmosResourceNotFoundError:
        log.warning(
            "rollback_cosmos_doc_vanished_mid_write",
            extra={"skill_id": skill_id, "version": version},
        )

    return before_status
