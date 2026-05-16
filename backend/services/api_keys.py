"""API-key service: thin wrapper around the auth-layer primitives with audit."""

from __future__ import annotations

from azure.cosmos.aio import ContainerProxy

from backend.core.auth.api_keys import issue as auth_issue
from backend.core.auth.api_keys import revoke as auth_revoke
from backend.core.config import Settings
from backend.models.api_key import (
    ApiKeyDoc,
    ApiKeyIssueRequest,
    ApiKeyIssueResponse,
    ApiKeyListItem,
)
from backend.services import audit as audit_svc


async def issue(
    *,
    body: ApiKeyIssueRequest,
    actor: str,
    actor_oid: str | None = None,
    api_keys: ContainerProxy,
    audit: ContainerProxy,
    settings: Settings,
) -> ApiKeyIssueResponse:
    doc, raw = await auth_issue(
        name=body.name,
        scopes=list(body.scopes),
        actor=actor,
        api_keys=api_keys,
        settings=settings,
    )
    # Audit row. We use the action label `apikey_issue` and a synthetic
    # skill_id so it routes to a stable partition without polluting any
    # real skill's audit stream.
    await audit_svc.record(
        audit,
        skill_id=f"apikey:{doc.key_id}",
        action="apikey_issue",
        actor=actor,
        actor_oid=actor_oid,
        after={"name": doc.name, "scopes": list(doc.scopes)},
    )
    return ApiKeyIssueResponse(
        key_id=doc.key_id,
        name=doc.name,
        scopes=list(doc.scopes),
        raw_key=raw,
        created_at=doc.created_at,
    )


async def revoke(
    *,
    key_id: str,
    actor: str,
    actor_oid: str | None = None,
    api_keys: ContainerProxy,
    audit: ContainerProxy,
    redis=None,
) -> ApiKeyDoc:
    doc = await auth_revoke(key_id=key_id, actor=actor, api_keys=api_keys, redis=redis)
    await audit_svc.record(
        audit,
        skill_id=f"apikey:{key_id}",
        action="apikey_revoke",
        actor=actor,
        actor_oid=actor_oid,
        after={"revoked_at": doc.revoked_at.isoformat() if doc.revoked_at else None},
    )
    return doc


async def list_keys(*, api_keys: ContainerProxy) -> list[ApiKeyListItem]:
    out: list[ApiKeyListItem] = []
    async for item in api_keys.query_items(
        query="SELECT * FROM c"
    ):
        doc = ApiKeyDoc.model_validate(item)
        out.append(
            ApiKeyListItem(
                key_id=doc.key_id,
                name=doc.name,
                scopes=list(doc.scopes),
                created_by=doc.created_by,
                created_at=doc.created_at,
                revoked_at=doc.revoked_at,
                last_used_at=doc.last_used_at,
            )
        )
    return out
