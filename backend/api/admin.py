"""Manager + admin endpoints: queue, approve, reject, classification override."""

from __future__ import annotations

from azure.cosmos.aio import ContainerProxy
from azure.storage.blob.aio import BlobServiceClient
from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from backend.core.auth import User, require_role
from backend.core.config import Settings
from backend.core.deps import (
    get_audit_container,
    get_blob,
    get_redis_client,
    get_skills_container,
    settings_dep,
)
from backend.models.api import (
    ApproveRequest,
    ArchiveRequest,
    ClassificationPatch,
    RejectRequest,
    SkillListItem,
)
from backend.services import catalog as catalog_svc
from backend.services import classification as classification_svc
from backend.services import curator as curator_svc
from backend.services import publish as publish_svc

router = APIRouter(prefix="/v1/admin", tags=["admin"])

_require_admin = require_role("admin")


@router.get("/queue", response_model=list[SkillListItem])
async def review_queue(
    _user: User = Depends(_require_admin),
    skills: ContainerProxy = Depends(get_skills_container),
) -> list[SkillListItem]:
    return await catalog_svc.list_review_queue(skills=skills)


@router.post("/skills/{skill_id}/approve", response_model=SkillListItem)
async def approve_skill(
    skill_id: str,
    _body: ApproveRequest | None = None,
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    doc = await publish_svc.publish(
        skill_id=skill_id,
        actor=user.email,
        actor_oid=user.oid,
        settings=settings,
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
    )
    return _to_item(doc)


@router.post("/skills/{skill_id}/reject", response_model=SkillListItem)
async def reject_skill(
    skill_id: str,
    body: RejectRequest,
    user: User = Depends(_require_admin),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
) -> SkillListItem:
    doc = await publish_svc.reject(
        skill_id=skill_id,
        actor=user.email,
        actor_oid=user.oid,
        reason=body.reason,
        skills=skills,
        audit=audit,
    )
    return _to_item(doc)


@router.patch("/skills/{skill_id}/classification", response_model=SkillListItem)
async def patch_classification(
    skill_id: str,
    patch: ClassificationPatch,
    user: User = Depends(_require_admin),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
) -> SkillListItem:
    doc = await classification_svc.apply_classification_patch(
        skill_id=skill_id,
        patch=patch,
        actor=user.email,
        actor_oid=user.oid,
        skills=skills,
        audit=audit,
    )
    return _to_item(doc)


@router.post("/skills/{skill_id}/archive", response_model=SkillListItem)
async def archive_skill(
    skill_id: str,
    body: ArchiveRequest,
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    """Admin-issued manual archive of an approved skill.

    Soft delete: bytes are copied to `archive/` and status flips to
    `archived`; the published bundle is intentionally left in place for
    defense-in-depth (AGENTS.md §5). Restorable via
    `POST /v1/admin/curator/restore/{skill_id}`.

    Refuses pinned skills (`SKILL_PINNED`) and non-approved skills
    (`INVALID_STATUS_TRANSITION`).
    """
    doc = await curator_svc.archive_skill_now(
        skill_id=skill_id,
        actor=user.email,
        actor_oid=user.oid,
        reason=body.reason,
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
        settings=settings,
    )
    return _to_item(doc)


def _to_item(doc) -> SkillListItem:
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
