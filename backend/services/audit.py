"""Append-only audit writes (AGENTS.md §8).

Every state transition MUST call `record(...)` in the same logical operation.
Integration tests assert audit row counts to catch regressions.
"""

from __future__ import annotations

from typing import Any

from azure.cosmos.aio import ContainerProxy

from backend.models.audit import AuditAction, AuditRecord


async def record(
    audit: ContainerProxy,
    *,
    skill_id: str,
    action: AuditAction,
    actor: str,
    actor_oid: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditRecord:
    """Write one immutable audit row.

    `actor` is the human-readable identity (email or `svc:<id>` or `system:*`).
    `actor_oid` is the immutable Entra object id for humans authenticated via
    OIDC; pass `None` for system/service actors and stub-mode callers.
    """
    rec = AuditRecord(
        skill_id=skill_id,
        action=action,
        actor=actor,
        actor_oid=actor_oid,
        before=before,
        after=after,
        metadata=metadata,
    )
    await audit.create_item(body=rec.model_dump(mode="json"))
    return rec
