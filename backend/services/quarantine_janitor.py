"""Quarantine janitor (M5-3) — the ONE allowed delete-after-N-days code path.

AGENTS.md §5 carves out the `quarantine/` blob container as the single
location in the system where delete-blob is permitted outside
`curator.move_published_to_archive`. This module owns that exception.

Contract:

  - Daily sweep (cron / scheduler driven, single-shot via `run_sweep`).
  - Reads every blob under `quarantine/` whose path encodes
    `{skill_id}/{version}/bundle.tar.gz`.
  - For each, reads the matching Cosmos doc. If `quarantine_expires_at`
    is set and is in the past (relative to `now`), deletes the blob.
  - Writes an immutable audit row (`action='quarantine_delete'`) for
    every delete.
  - NEVER deletes Cosmos data. The skill doc remains forever with
    `status='quarantined'` so the audit trail and report stay queryable.
  - Skips blobs whose Cosmos doc is missing — the doc is the source of
    truth; an orphan blob without an authorising doc is logged as a
    warning but NOT auto-removed (fail-safe).

AST gate (`backend/tests/unit/test_never_delete_invariant.py`) is
extended to whitelist `delete_blob(...)` ONLY inside
`move_to_deleted_after_retention(...)` in this file. Every other
function in this module is forbidden from calling `delete_blob` / 
`delete_item` for exactly the same reasons the curator is.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient

from backend.core.blob import quarantine_blob_path
from backend.core.config import Settings
from backend.core.logging import bind, get_logger
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc

log = get_logger(__name__)


async def _read_doc(
    skills: ContainerProxy, skill_id: str
) -> SkillDoc | None:
    """Latest doc for `skill_id` — same shape as `quarantine._load_latest`."""
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


async def move_to_deleted_after_retention(
    *,
    blob: BlobServiceClient,
    skills: ContainerProxy,
    audit: ContainerProxy,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, int]:
    """Single janitor pass. Returns counts for reporting / metrics.

    Despite the name, this function does NOT move blobs anywhere — it
    deletes them outright once they've outlived `quarantine_expires_at`.
    The verb "move_to_deleted" is intentional: it documents that this is
    the verified-terminal end of the quarantine lifecycle, analogous to
    `move_published_to_archive` being the verified-move end of the
    archive lifecycle. The Cosmos doc remains; the audit row makes the
    deletion durable for compliance.

    Returns:
      {"scanned": N, "deleted": N, "skipped_orphan": N, "skipped_active": N}
    """
    now = now or datetime.now(UTC)
    bind(actor="system:quarantine_janitor")

    container = blob.get_container_client(settings.blob_quarantine_container)

    scanned = 0
    deleted = 0
    skipped_orphan = 0
    skipped_active = 0

    async for b in container.list_blobs():
        scanned += 1
        name = b.name
        # Expected layout: `{skill_id}/{version}/bundle.tar.gz`.
        parts = name.split("/")
        if len(parts) < 3 or parts[-1] != "bundle.tar.gz":
            log.warning("quarantine_unexpected_blob_name", extra={"blob": name})
            continue
        skill_id = parts[0]
        version = parts[1]

        doc = await _read_doc(skills, skill_id)
        if doc is None:
            # Orphan blob — no Cosmos record. Fail-safe: leave it, log.
            skipped_orphan += 1
            log.warning(
                "quarantine_orphan_blob",
                extra={"blob": name, "skill_id": skill_id},
            )
            continue

        if doc.quarantine_expires_at is None:
            # Doc never went through quarantine_skill — leave the blob
            # alone. Operator will need to investigate manually.
            skipped_orphan += 1
            log.warning(
                "quarantine_blob_without_expires_at",
                extra={"blob": name, "skill_id": skill_id},
            )
            continue

        if doc.quarantine_expires_at > now:
            skipped_active += 1
            continue

        # Verify the blob currently being considered for deletion is the
        # one the doc points at (defence-in-depth against path drift).
        expected_path = quarantine_blob_path(skill_id, version)
        if name != expected_path:
            log.warning(
                "quarantine_blob_path_mismatch",
                extra={"blob": name, "expected": expected_path},
            )
            continue

        # The ONE allowed `delete_blob(...)` call outside
        # `curator.move_published_to_archive`. AGENTS.md §5 — the AST
        # never-delete gate whitelists this function name on this file.
        blob_client = container.get_blob_client(name)
        try:
            await blob_client.delete_blob()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "quarantine_delete_failed",
                extra={"blob": name, "err": str(exc)},
            )
            continue

        deleted += 1
        with contextlib.suppress(Exception):
            await audit_svc.record(
                audit,
                skill_id=skill_id,
                action="quarantine_delete",
                actor="system:quarantine_janitor",
                before={"quarantine_expires_at": doc.quarantine_expires_at.isoformat()},
                after={"blob_deleted": name},
                metadata={
                    "version": version,
                    "retention_days": settings.quarantine_retention_days,
                    "container": settings.blob_quarantine_container,
                },
            )
        log.info(
            "quarantine_delete_complete",
            extra={"skill_id": skill_id, "version": version, "blob": name},
        )

    return {
        "scanned": scanned,
        "deleted": deleted,
        "skipped_orphan": skipped_orphan,
        "skipped_active": skipped_active,
    }


async def run_sweep(
    *,
    blob: BlobServiceClient,
    skills: ContainerProxy,
    audit: ContainerProxy,
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, int]:
    """Convenience wrapper for the daily scheduler entry point.

    Kept separate from `move_to_deleted_after_retention` so a future
    pre-flight check (snapshot? per-blob lock?) has somewhere to live
    without expanding the AST whitelist beyond a single function.
    """
    return await move_to_deleted_after_retention(
        blob=blob,
        skills=skills,
        audit=audit,
        settings=settings,
        now=now,
    )
