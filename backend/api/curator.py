"""Admin curator endpoints — /v1/admin/curator/*.

Every endpoint is gated by `require_role("admin")`. Mirrors the shape of
`backend/api/admin.py`.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient
from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis

from backend.core.auth import User, require_role
from backend.core.blob import published_blob_path
from backend.core.config import Settings
from backend.core.deps import (
    get_audit_container,
    get_blob,
    get_redis_client,
    get_skills_container,
    get_system_state_container,
    settings_dep,
)
from backend.core.errors import SkillNotFound
from backend.core.logging import get_logger
from backend.core.redis import (
    key_cache_item,
    key_cache_list,
    key_curator_pause,
    key_curator_run_lock,
)
from backend.models.api import SkillListItem
from backend.models.curator import CuratorRunRecord, CuratorStatus, RollbackResult
from backend.models.skill import SkillDoc
from backend.services import (
    catalog as catalog_svc,
)
from backend.services import (
    curator as curator_svc,
)
from backend.services import (
    curator_rollback as curator_rollback_svc,
)
from backend.services import (
    curator_state as curator_state_svc,
)
from backend.services import (
    janitor as janitor_svc,
)
from backend.services.cosmos_helpers import replace_with_etag_retry

router = APIRouter(prefix="/v1/admin/curator", tags=["curator"])
log = get_logger(__name__)

_require_admin = require_role("admin")


def _to_item(doc: SkillDoc) -> SkillListItem:
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
    )


@router.post("/pause", response_model=CuratorStatus)
async def pause(
    user: User = Depends(_require_admin),
    audit: ContainerProxy = Depends(get_audit_container),
    system_state: ContainerProxy = Depends(get_system_state_container),
    redis: Redis = Depends(get_redis_client),
) -> CuratorStatus:
    await curator_state_svc.pause(
        system_state=system_state, audit=audit, redis=redis, actor=user.email
    )
    return CuratorStatus(paused=True, lock_held=False, schedule_enabled=True)


@router.post("/resume", response_model=CuratorStatus)
async def resume(
    user: User = Depends(_require_admin),
    audit: ContainerProxy = Depends(get_audit_container),
    system_state: ContainerProxy = Depends(get_system_state_container),
    redis: Redis = Depends(get_redis_client),
) -> CuratorStatus:
    await curator_state_svc.resume(
        system_state=system_state, audit=audit, redis=redis, actor=user.email
    )
    return CuratorStatus(paused=False, lock_held=False, schedule_enabled=True)


@router.post("/run", response_model=CuratorRunRecord)
async def run(
    dry_run: bool = Query(False),
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
    system_state: ContainerProxy = Depends(get_system_state_container),
) -> CuratorRunRecord:
    return await curator_svc.execute_pass(
        dry_run=dry_run,
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
        system_state=system_state,
        settings=settings,
        actor=user.email,
    )


@router.post("/rollback", response_model=RollbackResult)
async def rollback(
    id: str | None = Query(None),  # noqa: A002 — query param name is fine
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
) -> RollbackResult:
    return await curator_rollback_svc.rollback(
        snapshot_name=id,
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
        settings=settings,
        actor=user.email,
    )


@router.post("/restore/{skill_id}", response_model=SkillListItem)
async def restore_skill(
    skill_id: str,
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    """Restore a single archived skill: copy archive/→published/, flip status."""
    # Locate doc
    rows = []
    async for raw in skills.query_items(
        query="SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC",
        parameters=[{"name": "@id", "value": skill_id}],
        partition_key=skill_id,
    ):
        rows.append(raw)
        break
    if not rows:
        raise SkillNotFound(f"skill {skill_id!r} not found")
    doc = SkillDoc.model_validate(rows[0])
    if doc.status != "archived":
        return _to_item(doc)

    # Copy archive → published
    archive_path = published_blob_path(skill_id, doc.version)
    src = blob.get_container_client(settings.blob_archive_container).get_blob_client(
        archive_path
    )
    downloader = await src.download_blob()
    data = await downloader.readall()
    dest = blob.get_container_client(
        settings.blob_published_container
    ).get_blob_client(archive_path)
    await dest.upload_blob(data, overwrite=True)

    def _flip(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        d.status = "approved"
        return d.model_dump(mode="json")

    await replace_with_etag_retry(
        skills,
        item_id=doc.id,
        partition_key=skill_id,
        mutate=_flip,
    )

    from backend.services import audit as audit_svc

    await audit_svc.record(
        audit,
        skill_id=skill_id,
        action="restore",
        actor=user.email,
        before={"status": "archived"},
        after={"status": "approved"},
    )

    with contextlib.suppress(Exception):
        await redis.delete(key_cache_list(), key_cache_item(skill_id))

    doc.status = "approved"
    return _to_item(doc)


async def _flip_pinned(
    *,
    skill_id: str,
    pinned: bool,
    actor: str,
    skills: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
) -> SkillDoc:
    # Find latest doc
    rows = []
    async for raw in skills.query_items(
        query="SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC",
        parameters=[{"name": "@id", "value": skill_id}],
        partition_key=skill_id,
    ):
        rows.append(raw)
        break
    if not rows:
        raise SkillNotFound(f"skill {skill_id!r} not found")
    doc_id = rows[0]["id"]

    def _flip(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        d.pinned = pinned
        d.pinned_by = actor if pinned else None
        return d.model_dump(mode="json")

    updated = await replace_with_etag_retry(
        skills, item_id=doc_id, partition_key=skill_id, mutate=_flip
    )

    from backend.services import audit as audit_svc

    await audit_svc.record(
        audit,
        skill_id=skill_id,
        action="pin" if pinned else "unpin",
        actor=actor,
        after={"pinned": pinned},
    )

    with contextlib.suppress(Exception):
        await redis.delete(key_cache_list(), key_cache_item(skill_id))

    return SkillDoc.model_validate(updated)


@router.post("/pin/{skill_id}", response_model=SkillListItem)
async def pin_skill(
    skill_id: str,
    user: User = Depends(_require_admin),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    doc = await _flip_pinned(
        skill_id=skill_id,
        pinned=True,
        actor=user.email,
        skills=skills,
        audit=audit,
        redis=redis,
    )
    return _to_item(doc)


@router.post("/unpin/{skill_id}", response_model=SkillListItem)
async def unpin_skill(
    skill_id: str,
    user: User = Depends(_require_admin),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    doc = await _flip_pinned(
        skill_id=skill_id,
        pinned=False,
        actor=user.email,
        skills=skills,
        audit=audit,
        redis=redis,
    )
    return _to_item(doc)


@router.get("/status", response_model=CuratorStatus)
async def status_endpoint(
    _user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
    system_state: ContainerProxy = Depends(get_system_state_container),
) -> CuratorStatus:
    paused = await curator_state_svc.is_paused(
        system_state=system_state, redis=redis
    )

    lock_held = False
    try:
        lock_held = (await redis.get(key_curator_run_lock())) is not None
    except Exception:  # noqa: BLE001
        lock_held = False

    last_run: CuratorRunRecord | None = None
    try:
        container = blob.get_container_client(settings.curator_reports_container)
        prefix = f"{settings.curator_runs_container_prefix}/"
        names: list[str] = []
        async for b in container.list_blobs(name_starts_with=prefix):
            if b.name.endswith("/run.json"):
                names.append(b.name)
        if names:
            latest = sorted(names)[-1]
            client = container.get_blob_client(latest)
            downloader = await client.download_blob()
            raw = await downloader.readall()
            with contextlib.suppress(Exception):
                last_run = CuratorRunRecord.model_validate(json.loads(raw))
    except Exception:  # noqa: BLE001 — best-effort
        last_run = None

    return CuratorStatus(
        paused=paused,
        lock_held=lock_held,
        last_run=last_run,
        schedule_enabled=True,
        schedule_next=None,
    )


@router.post("/janitor")
async def janitor(
    _user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> dict[str, int]:
    return await janitor_svc.janitor_classifier_queue(
        skills=skills,
        audit=audit,
        redis=redis,
        settings=settings,
    )


# Silence unused-import warnings — these are imported for re-export wiring.
_ = key_curator_pause  # noqa: F841
_ = catalog_svc  # noqa: F841
