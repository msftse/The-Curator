"""Upload service.

Critical ordering (AGENTS.md §4 rule #1 + rule #4):
    1. Validate the bundle (pure).
    2. Create the pending Cosmos doc (source of truth).
    3. Write `upload` audit row.
    4. RPUSH the classifier queue.

If step 4 fails after step 2 succeeded, we do NOT roll back Cosmos — the M2
janitor sweep will re-queue pending docs that never got classified.
"""

from __future__ import annotations

import base64
import uuid

from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis

from backend.core.config import Settings
from backend.core.logging import bind, get_logger
from backend.core.redis import key_queue_classifier
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc
from backend.services.skill_bundle import (
    build_tar,
    enforce_size,
    extract_tar,
    looks_like_tar,
    parse_skill_md,
    slugify,
)

log = get_logger(__name__)


async def handle_upload(
    *,
    filename: str,
    data: bytes,
    uploader: str,
    uploader_oid: str | None = None,
    settings: Settings,
    skills: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
) -> SkillDoc:
    """Validate + persist a pending skill, then enqueue classification."""
    enforce_size(data, settings.max_bundle_bytes)

    files = _materialize_files(filename, data)
    if "SKILL.md" not in files:
        from backend.core.errors import InvalidBundle

        raise InvalidBundle("bundle must contain a SKILL.md at the root")

    skill_md_text = files["SKILL.md"].decode("utf-8", errors="replace")
    frontmatter, _body = parse_skill_md(skill_md_text)

    name = str(frontmatter["name"]).strip()
    description = str(frontmatter.get("description", "")).strip()
    skill_id = slugify(name)
    version = str(frontmatter.get("version", "1.0.0")).strip() or "1.0.0"

    # Re-pack to a deterministic tar so publish can rebuild byte-identical bundles.
    tar_bytes, _checksum = build_tar(files)

    doc = SkillDoc(
        id=f"{skill_id}:{version}:{uuid.uuid4().hex[:8]}",
        skill_id=skill_id,
        version=version,
        name=name,
        description=description,
        status="pending",
        classifier_status="queued",
        uploader=uploader,
        skill_md_text=skill_md_text,
        pending_bundle_b64=base64.b64encode(tar_bytes).decode("ascii"),
    )

    bind(skill_id=skill_id, actor=uploader)

    # 1. Cosmos write FIRST (source of truth).
    await skills.create_item(body=doc.model_dump(mode="json"))

    # 2. Audit.
    await audit_svc.record(
        audit,
        skill_id=skill_id,
        action="upload",
        actor=uploader,
        actor_oid=uploader_oid,
        after={"status": "pending", "version": version, "doc_id": doc.id},
        metadata={"filename": filename, "size_bytes": len(data)},
    )

    # 3. Enqueue classifier job. Failure here is logged + swallowed; the
    #    Cosmos doc is the durable record (rule #4 mitigation).
    try:
        await redis.rpush(key_queue_classifier(), doc.id)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("classifier_enqueue_failed", extra={"err": str(exc)})

    return doc


def _materialize_files(filename: str, data: bytes) -> dict[str, bytes]:
    """Return a {path: bytes} map.

    Supports two shapes for M0:
    - Single SKILL.md upload → {"SKILL.md": data}
    - tar(.gz) bundle → extracted contents
    """
    lower = filename.lower()
    if lower.endswith(".md") or lower == "skill.md":
        return {"SKILL.md": data}
    if lower.endswith((".tar", ".tar.gz", ".tgz")) or looks_like_tar(data):
        return extract_tar(data)
    # Default: treat as raw SKILL.md.
    return {"SKILL.md": data}
