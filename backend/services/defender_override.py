"""Defender override service (M5-4).

Admin response to a defender ``flagged`` finding when the admin judges
the finding to be a false positive (or an acceptable risk). Flips
``defender_status`` back to ``clean`` so the normal approve flow can
run, and records an immutable audit row with the justification text.

This is the *override* path. The other branch from a flagged finding —
"the finding is real and the skill is malicious" — goes through
``backend.services.quarantine.quarantine_skill``.

Ordering (mirrors ``services/quarantine.py``; AGENTS.md §4 rule 1):

    1. Read latest doc.
    2. Validate preconditions (defender_status == 'flagged',
       justification length).
    3. Cosmos write — SOURCE OF TRUTH FLIP — defender_status='clean',
       skill status unchanged (caller subsequently approves).
    4. Audit row (action='defender_override') with original severity,
       justification, and the preserved DefenderReport id reference.
    5. Redis invalidation — LAST, non-fatal (AGENTS.md §4 rule 2).

There are NO blob mutations and NO ``delete_*`` calls in this module.
The DefenderReport stays inline on the doc — admins reviewing the
catalog detail page later still see "this skill was flagged X for Y
and overridden by Z because W". The audit row is the durable trail.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis
from redis.exceptions import RedisError

from backend.core.config import Settings
from backend.core.errors import (
    DefenderNotFlagged,
    JustificationRequired,
    SkillNotFound,
    SkillPinned,
)
from backend.core.logging import bind, get_logger
from backend.core.redis import key_cache_item, key_cache_list
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc
from backend.services.cosmos_helpers import replace_with_etag_retry
from backend.services.notifier import (
    build_event,
    enqueue_notification,
    make_idempotency_key,
)

log = get_logger(__name__)


async def _load_latest(skills: ContainerProxy, skill_id: str) -> SkillDoc | None:
    rows: list[dict[str, Any]] = []
    async for raw in skills.query_items(
        query="SELECT * FROM c WHERE c.skill_id=@id ORDER BY c.uploaded_at DESC",
        parameters=[{"name": "@id", "value": skill_id}],
        partition_key=skill_id,
    ):
        rows.append(raw)
        break
    if not rows:
        return None
    return SkillDoc.model_validate(rows[0])


async def override_defender(
    *,
    skill_id: str,
    actor: str,
    actor_oid: str | None = None,
    justification: str,
    settings: Settings,
    skills: ContainerProxy,
    audit: ContainerProxy,
    redis: Redis,
    now: datetime | None = None,
) -> SkillDoc:
    """Admin overrides a defender ``flagged`` finding.

    Raises:
      SkillNotFound: no doc for ``skill_id``.
      SkillPinned: skill is pinned. Pinning is absolute (AGENTS.md §5);
        unpin first.
      DefenderNotFlagged: ``defender_status != 'flagged'``. Override is
        a *response* to a flag — never an arbitrary state edit.
      JustificationRequired: justification shorter than
        ``Settings.quarantine_min_justification_chars`` (the same floor
        the quarantine endpoint enforces; plan §3 calls for ≥20 chars).
    """
    bind(actor=actor, skill_id=skill_id)
    now = now or datetime.now(UTC)

    justification = (justification or "").strip()
    min_chars = settings.quarantine_min_justification_chars
    if len(justification) < min_chars:
        raise JustificationRequired(
            f"justification must be at least {min_chars} characters; got {len(justification)}",
            metadata={"min_chars": min_chars, "got_chars": len(justification)},
        )

    doc = await _load_latest(skills, skill_id)
    if doc is None:
        raise SkillNotFound(f"skill {skill_id!r} not found")

    if doc.pinned:
        raise SkillPinned(
            f"skill {skill_id!r} is pinned; unpin before overriding defender",
            metadata={"pinned_by": doc.pinned_by},
        )

    if doc.defender_status != "flagged":
        raise DefenderNotFlagged(
            f"skill {skill_id!r} has defender_status={doc.defender_status!r}; "
            f"override requires defender_status='flagged'",
            metadata={"defender_status": doc.defender_status},
        )

    before = {
        "defender_status": doc.defender_status,
        "defender_severity": doc.defender_severity,
    }

    def _flip(body: dict[str, Any]) -> dict[str, Any]:
        d = SkillDoc.model_validate(body)
        # Flip defender_status to clean so the normal approve flow can
        # proceed. We preserve `defender_severity` + `defender_report`
        # for the audit trail: the catalog detail page (and any future
        # forensic review) still sees what the scanner found and the
        # fact that an admin acknowledged it.
        d.defender_status = "clean"
        return d.model_dump(mode="json")

    updated_raw = await replace_with_etag_retry(
        skills,
        item_id=doc.id,
        partition_key=doc.skill_id,
        mutate=_flip,
    )
    updated = SkillDoc.model_validate(updated_raw)

    # Audit row — always written. Carries the original severity + report
    # id so the trail survives a future "where did this come from?"
    # query without joining back to the (mutable) skill doc.
    with contextlib.suppress(Exception):
        await audit_svc.record(
            audit,
            skill_id=skill_id,
            action="defender_override",
            actor=actor,
            actor_oid=actor_oid,
            before=before,
            after={
                "defender_status": "clean",
                "defender_severity": doc.defender_severity,
            },
            metadata={
                "justification": justification,
                "version": doc.version,
                "defender_severity": doc.defender_severity,
                "defender_report_id": doc.defender_report_id,
                "source": "admin_manual",
                "overridden_at": now.isoformat(),
            },
        )

    # Cache invalidation — LAST, non-fatal (AGENTS.md §4 rule 2).
    with contextlib.suppress(RedisError, Exception):
        await redis.delete(key_cache_list(), key_cache_item(doc.skill_id))

    # Notifier producer — `admin.override` to other admins (who overrode
    # what). Fire-and-forget; Cosmos write above is the source of truth.
    await enqueue_notification(
        build_event(
            "admin.override",
            skill_id=skill_id,
            payload={
                "skill_id": skill_id,
                "version": doc.version,
                "name": doc.name,
                "overridden_by": actor,
                "justification": justification,
                "defender_severity": doc.defender_severity,
                "overridden_at": now.isoformat(),
            },
            idempotency_key=make_idempotency_key(
                "admin.override",
                skill_id=skill_id,
                version=doc.version,
                extra=doc.id,
            ),
        ),
        redis=redis,
    )

    log.info(
        "defender_override_complete",
        extra={
            "skill_id": skill_id,
            "version": doc.version,
            "severity": doc.defender_severity,
        },
    )
    return updated
