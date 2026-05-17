"""Contributor endpoints: upload, my-submissions."""

from __future__ import annotations

import logging

from azure.cosmos.aio import ContainerProxy
from fastapi import APIRouter, Depends, File, Form, UploadFile, status
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
from backend.models.skill import CATEGORY_TAXONOMY
from backend.services import catalog as catalog_svc
from backend.services import upload as upload_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["uploads"])


@router.get("/categories", response_model=list[str])
async def list_categories() -> list[str]:
    """Canonical category taxonomy for the upload UI dropdown.

    Sourced from `backend.models.skill.CATEGORY_TAXONOMY` so the API,
    classifier allow-list, and frontend stay in lockstep.
    """
    return list(CATEGORY_TAXONOMY)


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_skill(
    file: UploadFile = File(...),
    category: str | None = Form(default=None),
    tags: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> UploadResponse:
    data = await file.read()
    # `tags` is sent as a comma-separated string from the chip-input UI.
    # Empty / missing → no user tags. Validation/normalization lives in
    # `upload_svc.handle_upload` so the wire format is the only thing
    # this endpoint owns.
    tag_list = [t for t in (s.strip() for s in tags.split(",")) if t] if tags else []
    # Audit-grade structured log: rare event, valuable for diagnosing
    # frontend/multipart-payload mismatches (e.g. stale tabs, FormData
    # stripped by a misbehaving fetch wrapper). Keep at INFO.
    log.info(
        "upload.received filename=%s size=%d category=%r tags=%r uploader=%s",
        file.filename,
        len(data),
        category,
        tag_list,
        user.email,
    )
    doc = await upload_svc.handle_upload(
        filename=file.filename or "SKILL.md",
        data=data,
        uploader=user.email,
        uploader_oid=user.oid,
        user_category=category,
        user_tags=tag_list,
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
