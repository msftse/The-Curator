"""Public catalog endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis

from backend.core.auth import Principal, get_principal, require_scope
from backend.core.auth.models import principal_actor
from backend.core.blob import signed_download_url
from backend.core.config import Settings
from backend.core.deps import (
    get_blob,
    get_redis_client,
    get_skills_container,
    get_usage_container,
    settings_dep,
)
from backend.core.errors import NotImplementedM0, SkillNotFound
from backend.models.api import DownloadUrlResponse, SkillDetail, SkillListItem
from backend.models.curator import UsageEvent, UsageEventDoc
from backend.services import catalog as catalog_svc
from backend.services import usage as usage_svc

_require_usage_write = require_scope("usage:write")

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/skills", tags=["catalog"])


@router.get("", response_model=list[SkillListItem])
async def list_skills(
    _principal: Principal = Depends(get_principal),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    redis: Redis = Depends(get_redis_client),
) -> list[SkillListItem]:
    return await catalog_svc.list_approved(skills=skills, redis=redis, settings=settings)


@router.get("/{skill_id}", response_model=SkillDetail)
async def get_skill(
    skill_id: str,
    _principal: Principal = Depends(get_principal),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    redis: Redis = Depends(get_redis_client),
) -> SkillDetail:
    doc = await catalog_svc.get_skill(
        skill_id=skill_id,
        skills=skills,
        redis=redis,
        settings=settings,
    )
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")
    return SkillDetail(
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
        skill_md_text=doc.skill_md_text,
        # M5-4 — defender report + quarantine mirrors. The admin UI
        # renders these directly; safe to surface to non-admin readers
        # too (the report is an audit signal, not a secret).
        defender_status=doc.defender_status,
        defender_severity=doc.defender_severity,
        defender_report=doc.defender_report,
        defender_scanned_at=doc.defender_scanned_at,
        quarantined_at=doc.quarantined_at,
        quarantined_by=doc.quarantined_by,
        quarantine_justification=doc.quarantine_justification,
        quarantine_expires_at=doc.quarantine_expires_at,
    )


@router.get("/{skill_id}/download_url", response_model=DownloadUrlResponse)
async def get_download_url(
    skill_id: str,
    _principal: Principal = Depends(get_principal),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    redis: Redis = Depends(get_redis_client),
    blob: BlobServiceClient = Depends(get_blob),
) -> DownloadUrlResponse:
    """Return a short-lived SAS URL for the bundle, plus its expiry.

    Used by the SPA: the browser cannot attach the bearer token to a
    navigation/anchor download, so the SPA fetches the URL here (auth'd)
    and then sets `window.location` to the SAS. The SAS itself is the
    capability presented to Azure Blob.

    Default TTL is 15 minutes (see `signed_download_url`).
    """
    doc = await catalog_svc.get_skill(
        skill_id=skill_id,
        skills=skills,
        redis=redis,
        settings=settings,
    )
    if doc is None or doc.bundle is None or doc.status != "approved":
        raise SkillNotFound(f"skill {skill_id!r} not approved or has no bundle")
    try:
        url = await signed_download_url(blob, settings, skill_id=skill_id, version=doc.version)
    except Exception as exc:  # noqa: BLE001
        # Surface the underlying SAS-generation failure as a structured
        # 500 rather than letting FastAPI swallow it into an opaque
        # "Internal Server Error". Azurite vs. user-delegation-key edge
        # cases are common; the message is diagnostic, not user-facing.
        log.exception(
            "signed_download_url failed for skill_id=%s version=%s",
            skill_id,
            doc.version,
        )
        from backend.core.errors import DomainError

        class _DownloadUrlError(DomainError):
            error_code = "DOWNLOAD_URL_GENERATION_FAILED"
            http_status = 500

        raise _DownloadUrlError(
            f"could not mint signed URL: {exc.__class__.__name__}: {exc}",
            metadata={"skill_id": skill_id, "version": doc.version},
        ) from exc
    # `signed_download_url` defaults to TTL=15min; mirror that here. If the
    # helper signature ever exposes the expiry, prefer threading it through
    # over recomputing.
    expires_at = datetime.now(UTC) + timedelta(minutes=15)
    return DownloadUrlResponse(url=url, expires_at=expires_at)


@router.get("/{skill_id}/download")
async def download_skill(
    skill_id: str,
    _principal: Principal = Depends(get_principal),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    redis: Redis = Depends(get_redis_client),
    blob: BlobServiceClient = Depends(get_blob),
) -> RedirectResponse:
    doc = await catalog_svc.get_skill(
        skill_id=skill_id,
        skills=skills,
        redis=redis,
        settings=settings,
    )
    if doc is None or doc.bundle is None or doc.status != "approved":
        raise SkillNotFound(f"skill {skill_id!r} not approved or has no bundle")
    url = await signed_download_url(blob, settings, skill_id=skill_id, version=doc.version)
    return RedirectResponse(url=url, status_code=307)


@router.get("/{skill_id}/versions")
async def list_versions(skill_id: str) -> None:
    raise NotImplementedM0("versions endpoint will land in M2")


@router.post("/{skill_id}/usage", response_model=UsageEventDoc)
async def report_usage(
    skill_id: str,
    body: UsageEvent,
    principal: Principal = Depends(_require_usage_write),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    usage: ContainerProxy = Depends(get_usage_container),
    redis: Redis = Depends(get_redis_client),
) -> UsageEventDoc:
    doc = await catalog_svc.get_skill(
        skill_id=skill_id,
        skills=skills,
        redis=redis,
        settings=settings,
    )
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")
    actor = principal_actor(principal)
    return await usage_svc.record_usage_event(
        skill_id=skill_id,
        version=doc.version,
        loader_id=body.loader_id or actor,
        context=body.context,
        skills=skills,
        usage=usage,
        redis=redis,
        settings=settings,
    )


_ = NotImplementedM0  # retained for compatibility with other handlers
