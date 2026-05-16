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
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditRecord:
    """Write one immutable audit row."""
    rec = AuditRecord(
        skill_id=skill_id,
        action=action,
        actor=actor,
        before=before,
        after=after,
        metadata=metadata,
    )
    await audit.create_item(body=rec.model_dump(mode="json"))
    return rec
