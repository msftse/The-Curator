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
from fastapi.responses import Response
from redis.asyncio import Redis

from backend.core.auth import User, require_role
from backend.core.blob import published_blob_path
from backend.core.config import Settings
from backend.core.deps import (
    get_audit_container,
    get_blob,
    get_llm_provider,
    get_redis_client,
    get_review_proposals_container,
    get_skills_container,
    get_system_state_container,
    settings_dep,
)
from backend.core.errors import (
    ReviewProposalNotFound,
    ReviewProposalNotPending,
    SkillNotFound,
)
from backend.core.logging import get_logger
from backend.core.redis import (
    key_cache_item,
    key_cache_list,
    key_curator_pause,
    key_curator_run_lock,
)
from backend.models.api import SkillListItem
from backend.models.curator import (
    CuratorRunDispatched,
    CuratorRunRecord,
    CuratorStatus,
    RollbackResult,
    SnapshotListItem,
)
from backend.models.review import (
    CuratorReviewRunRecord,
    ReviewListResponse,
    ReviewProposal,
)
from backend.models.schedule import CuratorSchedule, CuratorScheduleUpdate
from backend.models.skill import SkillDoc
from backend.services import (
    catalog as catalog_svc,
)
from backend.services import (
    curator as curator_svc,
)
from backend.services import (
    curator_review as curator_review_svc,
)
from backend.services import (
    curator_review_apply as curator_review_apply_svc,
)
from backend.services import (
    curator_rollback as curator_rollback_svc,
)
from backend.services import (
    curator_schedule as curator_schedule_svc,
)
from backend.services import (
    curator_state as curator_state_svc,
)
from backend.services import (
    janitor as janitor_svc,
)
from backend.services import (
    snapshot as snapshot_svc,
)
from backend.services.cosmos_helpers import replace_with_etag_retry
from backend.services.llm import LLMProvider

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
        user_category=doc.user_category,
        user_tags=list(doc.user_tags),
        defender_status=doc.defender_status,
        defender_severity=doc.defender_severity,
        defender_report=doc.defender_report,
        defender_scanned_at=doc.defender_scanned_at,
    )


@router.post("/pause", response_model=CuratorStatus)
async def pause(
    user: User = Depends(_require_admin),
    audit: ContainerProxy = Depends(get_audit_container),
    system_state: ContainerProxy = Depends(get_system_state_container),
    redis: Redis = Depends(get_redis_client),
) -> CuratorStatus:
    await curator_state_svc.pause(
        system_state=system_state, audit=audit, redis=redis, actor=user.email, actor_oid=user.oid
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
        system_state=system_state, audit=audit, redis=redis, actor=user.email, actor_oid=user.oid
    )
    return CuratorStatus(paused=False, lock_held=False, schedule_enabled=True)


@router.post("/run", response_model=CuratorRunRecord | CuratorRunDispatched)
async def run(
    dry_run: bool = Query(False),
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
    system_state: ContainerProxy = Depends(get_system_state_container),
) -> CuratorRunRecord | CuratorRunDispatched:
    # M4: in `k8s` runtime mode, dispatch a K8s Job rather than blocking the
    # API request on the curator pass. Admin role gate (above, via
    # `_require_admin`) is the first check — non-admins return 403 before we
    # ever touch the K8s API. See plan §M4 Task 18 + services/k8s_jobs.py.
    if settings.runtime_mode == "k8s":
        # Lazy import — `kubernetes` is an optional extra not installed in
        # local-dev `uv sync`. Module-level import would break `make api`.
        from backend.services import k8s_jobs

        handle = k8s_jobs.create_curator_ondemand_job(
            settings=settings,
            dry_run=dry_run,
            actor=user.email,
        )
        return CuratorRunDispatched(
            job_name=handle["job_name"],
            namespace=handle["namespace"],
            dry_run=dry_run,
        )

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
    src = blob.get_container_client(settings.blob_archive_container).get_blob_client(archive_path)
    downloader = await src.download_blob()
    data = await downloader.readall()
    dest = blob.get_container_client(settings.blob_published_container).get_blob_client(
        archive_path
    )
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
        actor_oid=user.oid,
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
    actor_oid: str | None = None,
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
        actor_oid=actor_oid,
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
        actor_oid=user.oid,
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
        actor_oid=user.oid,
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
    paused = await curator_state_svc.is_paused(system_state=system_state, redis=redis)

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


@router.get("/schedule", response_model=CuratorSchedule)
async def get_schedule_endpoint(
    _user: User = Depends(_require_admin),
    system_state: ContainerProxy = Depends(get_system_state_container),
) -> CuratorSchedule:
    """Return the curator schedule doc (default = weekly Sunday 03:00 UTC).

    The K8s CronJob's `.spec.schedule` is reconciled to this value by the
    `curator_schedule_reconciler` worker; the field returned here is the
    desired schedule, not the live CronJob spec.
    """
    return await curator_schedule_svc.get_schedule(system_state=system_state)


@router.put("/schedule", response_model=CuratorSchedule)
async def put_schedule_endpoint(
    body: CuratorScheduleUpdate,
    user: User = Depends(_require_admin),
    system_state: ContainerProxy = Depends(get_system_state_container),
    audit: ContainerProxy = Depends(get_audit_container),
) -> CuratorSchedule:
    """Replace the curator schedule. Admin-only.

    Cron syntax is validated by `CuratorScheduleUpdate` (Pydantic) — a
    422 with the field error is returned for malformed input. The
    Cosmos-first write happens before the audit row (AGENTS.md §4 rule 1),
    and the immutable audit record carries `before`/`after` for diffing.
    The K8s CronJob is patched out-of-band by
    `backend/workers/curator_schedule_reconciler.py`; this endpoint does
    not touch the K8s API directly.
    """
    return await curator_schedule_svc.put_schedule(
        system_state=system_state,
        audit=audit,
        actor=user.email,
        actor_oid=user.oid,
        cron=body.cron,
        timezone=body.timezone,
        enabled=body.enabled,
    )


@router.get("/snapshots", response_model=list[SnapshotListItem])
async def list_snapshots_endpoint(
    _user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    blob: BlobServiceClient = Depends(get_blob),
) -> list[SnapshotListItem]:
    """List snapshot folders, newest first.

    For each folder under `{blob_snapshots_container}/` (excluding the
    `_retired/` prefix) read its `manifest.json` for `captured_at` +
    `skills_count`, and the `skills.tar.gz` blob properties for
    `size_bytes`. Snapshots missing a manifest are skipped silently —
    they're either mid-write or operator-created stubs.
    """
    names = await snapshot_svc.list_snapshots(blob, settings)
    container = blob.get_container_client(settings.blob_snapshots_container)
    items: list[SnapshotListItem] = []
    for name in names:
        try:
            manifest = await snapshot_svc.load_manifest(blob, settings, name)
        except Exception:  # noqa: BLE001 — best-effort listing
            continue
        size_bytes = 0
        try:
            tar_client = container.get_blob_client(f"{name}/skills.tar.gz")
            props = await tar_client.get_blob_properties()
            size_bytes = int(props.size or 0)
        except Exception:  # noqa: BLE001 — size is informational
            size_bytes = 0
        items.append(
            SnapshotListItem(
                name=name,
                captured_at=manifest.captured_at,
                skills_count=len(manifest.skills),
                size_bytes=size_bytes,
            )
        )
    return items


@router.get("/runs", response_model=list[CuratorRunRecord])
async def list_runs_endpoint(
    limit: int = Query(50, ge=1, le=500),
    _user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    blob: BlobServiceClient = Depends(get_blob),
) -> list[CuratorRunRecord]:
    """List recent curator runs, newest first.

    Each run lives at `{curator_reports_container}/runs/{run_id}/run.json`.
    """
    container = blob.get_container_client(settings.curator_reports_container)
    prefix = f"{settings.curator_runs_container_prefix}/"
    names: list[str] = []
    try:
        async for b in container.list_blobs(name_starts_with=prefix):
            if b.name.endswith("/run.json"):
                names.append(b.name)
    except Exception as exc:  # noqa: BLE001 — best-effort listing
        # AuthorizationFailure (missing Storage Blob Data Reader), container
        # not yet provisioned, or transient network issue. Return [] so the
        # UI renders "no prior runs" instead of nuking CORS via a 500.
        log.warning("curator.list_runs.list_failed", extra={"error": str(exc)})
        return []
    # Lexicographic sort works because run_ids are UTC-iso-compact timestamps.
    names.sort(reverse=True)
    out: list[CuratorRunRecord] = []
    for blob_name in names[:limit]:
        try:
            client = container.get_blob_client(blob_name)
            downloader = await client.download_blob()
            raw = await downloader.readall()
            out.append(CuratorRunRecord.model_validate(json.loads(raw)))
        except Exception:  # noqa: BLE001 — skip unreadable run records
            continue
    return out


@router.get("/runs/{run_id}/report")
async def get_run_report(
    run_id: str,
    _user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    blob: BlobServiceClient = Depends(get_blob),
) -> Response:
    """Return the rendered Markdown report for a curator run."""
    from backend.core.errors import CuratorRunReportNotFound

    container = blob.get_container_client(settings.curator_reports_container)
    blob_path = f"{settings.curator_runs_container_prefix}/{run_id}/REPORT.md"
    try:
        client = container.get_blob_client(blob_path)
        downloader = await client.download_blob()
        raw = await downloader.readall()
    except Exception as exc:  # noqa: BLE001
        raise CuratorRunReportNotFound(f"report for run {run_id!r} not found") from exc
    return Response(content=raw, media_type="text/markdown; charset=utf-8")


@router.post("/janitor")
async def janitor(
    _user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> dict[str, dict[str, int]]:
    classifier_result = await janitor_svc.janitor_classifier_queue(
        skills=skills,
        audit=audit,
        redis=redis,
        settings=settings,
    )
    defender_result = await janitor_svc.janitor_defender_queue(
        skills=skills,
        audit=audit,
        redis=redis,
        settings=settings,
    )
    return {"classifier": classifier_result, "defender": defender_result}


# ---- M3 — Curator LLM review endpoints ---------------------------------


@router.post("/review", response_model=CuratorReviewRunRecord)
async def run_review(
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    provider: LLMProvider = Depends(get_llm_provider),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    review_proposals: ContainerProxy = Depends(get_review_proposals_container),
    system_state: ContainerProxy = Depends(get_system_state_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
) -> CuratorReviewRunRecord:
    return await curator_review_svc.execute_review_pass(
        provider=provider,
        skills=skills,
        audit=audit,
        review_proposals=review_proposals,
        system_state=system_state,
        blob=blob,
        redis=redis,
        settings=settings,
        actor=user.email,
    )


@router.get("/reviews", response_model=ReviewListResponse)
async def list_reviews(
    status: str | None = Query(None),
    run_id: str | None = Query(None),
    limit: int = Query(100, le=500),
    _user: User = Depends(_require_admin),
    review_proposals: ContainerProxy = Depends(get_review_proposals_container),
) -> ReviewListResponse:
    where: list[str] = []
    params: list[dict[str, Any]] = []
    if status:
        where.append("c.status=@status")
        params.append({"name": "@status", "value": status})
    if run_id:
        where.append("c.run_id=@run_id")
        params.append({"name": "@run_id", "value": run_id})
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    query = f"SELECT * FROM c{where_sql} ORDER BY c.created_at DESC OFFSET 0 LIMIT @limit"
    params.append({"name": "@limit", "value": int(limit)})

    proposals: list[ReviewProposal] = []
    async for raw in review_proposals.query_items(
        query=query,
        parameters=params,
    ):
        with contextlib.suppress(Exception):
            proposals.append(ReviewProposal.model_validate(raw))
    return ReviewListResponse(proposals=proposals, total=len(proposals))


@router.get("/reviews/{proposal_id}", response_model=ReviewProposal)
async def get_review(
    proposal_id: str,
    run_id: str = Query(...),
    _user: User = Depends(_require_admin),
    review_proposals: ContainerProxy = Depends(get_review_proposals_container),
) -> ReviewProposal:
    try:
        raw = await review_proposals.read_item(item=proposal_id, partition_key=run_id)
    except Exception as exc:  # noqa: BLE001
        raise ReviewProposalNotFound(
            f"proposal {proposal_id!r} (run_id={run_id!r}) not found"
        ) from exc
    return ReviewProposal.model_validate(raw)


@router.post("/reviews/{proposal_id}/approve", response_model=ReviewProposal)
async def approve_review(
    proposal_id: str,
    run_id: str = Query(...),
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    review_proposals: ContainerProxy = Depends(get_review_proposals_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
) -> ReviewProposal:
    try:
        raw = await review_proposals.read_item(item=proposal_id, partition_key=run_id)
    except Exception as exc:  # noqa: BLE001
        raise ReviewProposalNotFound(
            f"proposal {proposal_id!r} (run_id={run_id!r}) not found"
        ) from exc
    proposal = ReviewProposal.model_validate(raw)
    if proposal.kind == "patch":
        return await curator_review_apply_svc.apply_patch_proposal(
            proposal_id=proposal_id,
            run_id=run_id,
            actor=user.email,
            settings=settings,
            skills=skills,
            audit=audit,
            review_proposals=review_proposals,
            blob=blob,
            redis=redis,
        )
    if proposal.kind == "merge":
        return await curator_review_apply_svc.apply_merge_proposal(
            proposal_id=proposal_id,
            run_id=run_id,
            actor=user.email,
            settings=settings,
            skills=skills,
            audit=audit,
            review_proposals=review_proposals,
            blob=blob,
            redis=redis,
        )
    raise ReviewProposalNotPending(f"proposal kind={proposal.kind!r} cannot be applied")


@router.post("/reviews/{proposal_id}/reject", response_model=ReviewProposal)
async def reject_review(
    proposal_id: str,
    run_id: str = Query(...),
    reason: str = Query(""),
    user: User = Depends(_require_admin),
    audit: ContainerProxy = Depends(get_audit_container),
    review_proposals: ContainerProxy = Depends(get_review_proposals_container),
) -> ReviewProposal:
    return await curator_review_apply_svc.reject_proposal(
        proposal_id=proposal_id,
        run_id=run_id,
        actor=user.email,
        reason=reason,
        review_proposals=review_proposals,
        audit=audit,
    )


# Silence unused-import warnings — these are imported for re-export wiring.
_ = key_curator_pause  # noqa: F841
_ = catalog_svc  # noqa: F841
