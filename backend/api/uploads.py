"""Contributor endpoints: upload, my-submissions."""

from __future__ import annotations

from azure.cosmos.aio import ContainerProxy
from fastapi import APIRouter, Depends, File, UploadFile, status
from redis.asyncio import Redis

from backend.core.auth import User, get_current_user
from backend.core.config import Settings
from backend.core.deps import (
    get_audit_container,
    get_redis_client,
    get_skills_container,
    settings_dep,
)
from backend.models.api import SkillListItem, UploadResponse
from backend.services import catalog as catalog_svc
from backend.services import upload as upload_svc

router = APIRouter(prefix="/v1", tags=["uploads"])


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_skill(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> UploadResponse:
    data = await file.read()
    doc = await upload_svc.handle_upload(
        filename=file.filename or "SKILL.md",
        data=data,
        uploader=user.email,
        uploader_oid=user.oid,
        settings=settings,
        skills=skills,
        audit=audit,
        redis=redis,
    )
    return UploadResponse(
        skill_id=doc.skill_id,
        version=doc.version,
        status=doc.status,
        classifier_status=doc.classifier_status,
        uploaded_at=doc.uploaded_at,
    )


@router.get("/me/submissions", response_model=list[SkillListItem])
async def my_submissions(
    user: User = Depends(get_current_user),
    skills: ContainerProxy = Depends(get_skills_container),
) -> list[SkillListItem]:
    return await catalog_svc.list_my_submissions(uploader=user.email, skills=skills)
