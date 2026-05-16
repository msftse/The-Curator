"""Public catalog endpoints."""

from __future__ import annotations

from azure.cosmos.aio import ContainerProxy
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis

from backend.core.blob import signed_download_url
from backend.core.config import Settings
from backend.core.deps import get_redis_client, get_skills_container, settings_dep
from backend.core.errors import NotImplementedM0, SkillNotFound
from backend.models.api import SkillListItem
from backend.services import catalog as catalog_svc

router = APIRouter(prefix="/v1/skills", tags=["catalog"])


@router.get("", response_model=list[SkillListItem])
async def list_skills(
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    redis: Redis = Depends(get_redis_client),
) -> list[SkillListItem]:
    return await catalog_svc.list_approved(skills=skills, redis=redis, settings=settings)


@router.get("/{skill_id}", response_model=SkillListItem)
async def get_skill(
    skill_id: str,
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    doc = await catalog_svc.get_skill(
        skill_id=skill_id,
        skills=skills,
        redis=redis,
        settings=settings,
    )
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")
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


@router.get("/{skill_id}/download")
async def download_skill(
    skill_id: str,
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    redis: Redis = Depends(get_redis_client),
) -> RedirectResponse:
    doc = await catalog_svc.get_skill(
        skill_id=skill_id,
        skills=skills,
        redis=redis,
        settings=settings,
    )
    if doc is None or doc.bundle is None or doc.status != "approved":
        raise SkillNotFound(f"skill {skill_id!r} not approved or has no bundle")
    url = signed_download_url(settings, skill_id=skill_id, version=doc.version)
    return RedirectResponse(url=url, status_code=307)


@router.get("/{skill_id}/versions")
async def list_versions(skill_id: str) -> None:
    raise NotImplementedM0("versions endpoint will land in M2")


@router.post("/{skill_id}/usage")
async def report_usage(skill_id: str) -> None:
    raise NotImplementedM0("usage ingestion will land in M2")
