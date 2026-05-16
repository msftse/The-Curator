"""Apply a manager classification override to a pending/classified skill."""

from __future__ import annotations

from azure.cosmos.aio import ContainerProxy

from backend.core.errors import SkillNotFound
from backend.models.api import ClassificationPatch
from backend.models.skill import Classification, SkillDoc
from backend.services import audit as audit_svc


async def apply_classification_patch(
    *,
    skill_id: str,
    patch: ClassificationPatch,
    actor: str,
    actor_oid: str | None = None,
    skills: ContainerProxy,
    audit: ContainerProxy,
) -> SkillDoc:
    from backend.services.publish import _load_latest

    doc = await _load_latest(skills, skill_id)
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")
    before = doc.classification.model_dump(mode="json") if doc.classification else None
    current = doc.classification or Classification()
    merged = current.model_copy(update=dict(patch.model_dump(exclude_none=True).items()))
    doc.classification = merged
    if doc.status == "pending":
        doc.status = "classified"
    await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
    await audit_svc.record(
        audit,
        skill_id=skill_id,
        action="classify",
        actor=actor,
        actor_oid=actor_oid,
        before={"classification": before},
        after={"classification": merged.model_dump(mode="json")},
        metadata={"source": "manager_override"},
    )
    return doc
