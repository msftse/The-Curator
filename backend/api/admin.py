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
    DefenderOverrideRequest,
    QuarantineRequest,
    RejectRequest,
    SkillListItem,
)
from backend.services import catalog as catalog_svc
from backend.services import classification as classification_svc
from backend.services import classifier_requeue as classifier_requeue_svc
from backend.services import curator as curator_svc
from backend.services import defender_override as defender_override_svc
from backend.services import defender_requeue as defender_requeue_svc
from backend.services import publish as publish_svc
from backend.services import quarantine as quarantine_svc

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
    body: ApproveRequest | None = None,
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
        defender_override=body.defender_override if body else False,
        defender_justification=body.justification if body else None,
    )
    return _to_item(doc)


@router.post("/skills/{skill_id}/reject", response_model=SkillListItem)
async def reject_skill(
    skill_id: str,
    body: RejectRequest,
    user: User = Depends(_require_admin),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    doc = await publish_svc.reject(
        skill_id=skill_id,
        actor=user.email,
        actor_oid=user.oid,
        reason=body.reason,
        skills=skills,
        audit=audit,
        redis=redis,
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


@router.post("/skills/{skill_id}/classify", response_model=SkillListItem)
async def classify_now(
    skill_id: str,
    user: User = Depends(_require_admin),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    doc = await classifier_requeue_svc.requeue_classifier(
        skill_id=skill_id,
        actor=user.email,
        actor_oid=user.oid,
        skills=skills,
        audit=audit,
        redis=redis,
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

    Move semantics (AGENTS.md §5 "archive = move, not copy"):
    bytes are moved to `archive/` (copy → verify destination exists →
    delete source from `published/`) and status flips to `archived`.
    Restorable via `POST /v1/admin/curator/restore/{skill_id}`, which
    copies `archive/` → `published/` and flips status back to `approved`.

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


@router.post("/skills/{skill_id}/quarantine", response_model=SkillListItem)
async def quarantine_skill(
    skill_id: str,
    body: QuarantineRequest,
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    blob: BlobServiceClient = Depends(get_blob),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    """Admin response to a defender-flagged skill (M5-3).

    Preconditions:
      - Caller has `admin` role (enforced by dependency).
      - `defender_status == 'flagged'` — else 409 `DEFENDER_NOT_FLAGGED`.
      - `justification` length >= `quarantine_min_justification_chars`
        (default 20) — else 422 `JUSTIFICATION_REQUIRED`.
      - Skill not pinned — else 409 `SKILL_PINNED`.

    Effects (Cosmos-first, AGENTS.md §4 rule 1):
      1. Bundle bytes copied to `quarantine/{id}/{ver}/bundle.tar.gz`;
         destination verified before the Cosmos flip.
      2. Cosmos doc: `status='quarantined'`, `quarantined_at`,
         `quarantined_by`, `quarantine_justification`,
         `quarantine_expires_at = now + QUARANTINE_RETENTION_DAYS`.
      3. Immutable audit row (`action='quarantine'`) with the
         justification text.
      4. Catalog cache invalidated.

    The bundle bytes are deleted from `quarantine/` only after
    `quarantine_expires_at` by the dedicated quarantine janitor — the
    ONE delete-after-N-days code path in the system (AGENTS.md §5).
    The Cosmos doc itself is never deleted.
    """
    doc = await quarantine_svc.quarantine_skill(
        skill_id=skill_id,
        actor=user.email,
        actor_oid=user.oid,
        justification=body.justification,
        settings=settings,
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
    )
    return _to_item(doc)


@router.post("/skills/{skill_id}/defender-override", response_model=SkillListItem)
async def defender_override(
    skill_id: str,
    body: DefenderOverrideRequest,
    user: User = Depends(_require_admin),
    settings: Settings = Depends(settings_dep),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    """Admin overrides a defender-flagged finding (M5-4).

    Preconditions:
      - Caller has `admin` role (enforced by dependency).
      - `defender_status == 'flagged'` — else 409 `DEFENDER_NOT_FLAGGED`.
      - `justification` length >= `quarantine_min_justification_chars`
        (default 20) — else 422 `JUSTIFICATION_REQUIRED`.
      - Skill not pinned — else 409 `SKILL_PINNED`.

    Effects (Cosmos-only flip; AGENTS.md §4 rule 1):
      1. Cosmos doc: `defender_status='clean'`. `defender_severity` and
         `defender_report` are preserved so the audit trail and the
         catalog detail page still show what the scanner found.
      2. Immutable audit row (`action='defender_override'`) carrying
         the justification, original severity, and report id.
      3. Catalog cache invalidated.

    Skill `status` is *not* changed — the admin still has to call
    `POST /v1/admin/skills/{id}/approve` to publish. Override is the
    "I disagree with the scanner" signal; approve is the "ship it"
    signal. Keeping them separate keeps the audit narrative honest.
    """
    doc = await defender_override_svc.override_defender(
        skill_id=skill_id,
        actor=user.email,
        actor_oid=user.oid,
        justification=body.justification,
        settings=settings,
        skills=skills,
        audit=audit,
        redis=redis,
    )
    return _to_item(doc)


@router.post("/skills/{skill_id}/defender-rescan", response_model=SkillListItem)
async def defender_rescan(
    skill_id: str,
    user: User = Depends(_require_admin),
    skills: ContainerProxy = Depends(get_skills_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> SkillListItem:
    doc = await defender_requeue_svc.requeue_defender(
        skill_id=skill_id,
        actor=user.email,
        actor_oid=user.oid,
        skills=skills,
        audit=audit,
        redis=redis,
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
        user_category=doc.user_category,
        user_tags=list(doc.user_tags),
        defender_status=doc.defender_status,
        defender_severity=doc.defender_severity,
        defender_report=doc.defender_report,
        defender_scanned_at=doc.defender_scanned_at,
    )
