"""Admin-only API key CRUD endpoints (M1)."""

from __future__ import annotations

from azure.cosmos.aio import ContainerProxy
from fastapi import APIRouter, Depends, Request, status
from redis.asyncio import Redis

from backend.core.auth import User, require_role
from backend.core.config import Settings
from backend.core.deps import (
    get_audit_container,
    get_redis_client,
    settings_dep,
)
from backend.models.api_key import (
    ApiKeyIssueRequest,
    ApiKeyIssueResponse,
    ApiKeyListItem,
)
from backend.services import api_keys as api_keys_svc

router = APIRouter(prefix="/v1/admin/api-keys", tags=["admin", "api-keys"])

_require_admin = require_role("admin")


def get_api_keys_container(request: Request) -> ContainerProxy:
    container = getattr(request.app.state, "api_keys_container", None)
    if container is None:  # pragma: no cover — wired in lifespan
        raise RuntimeError("api_keys_container not initialised")
    return container


@router.post("", response_model=ApiKeyIssueResponse, status_code=status.HTTP_201_CREATED)
async def issue_key(
    body: ApiKeyIssueRequest,
    user: User = Depends(_require_admin),
    api_keys: ContainerProxy = Depends(get_api_keys_container),
    audit: ContainerProxy = Depends(get_audit_container),
    settings: Settings = Depends(settings_dep),
) -> ApiKeyIssueResponse:
    return await api_keys_svc.issue(
        body=body,
        actor=user.email,
        actor_oid=user.oid,
        api_keys=api_keys,
        audit=audit,
        settings=settings,
    )


@router.get("", response_model=list[ApiKeyListItem])
async def list_keys(
    _user: User = Depends(_require_admin),
    api_keys: ContainerProxy = Depends(get_api_keys_container),
) -> list[ApiKeyListItem]:
    return await api_keys_svc.list_keys(api_keys=api_keys)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(
    key_id: str,
    user: User = Depends(_require_admin),
    api_keys: ContainerProxy = Depends(get_api_keys_container),
    audit: ContainerProxy = Depends(get_audit_container),
    redis: Redis = Depends(get_redis_client),
) -> None:
    await api_keys_svc.revoke(
        key_id=key_id,
        actor=user.email,
        actor_oid=user.oid,
        api_keys=api_keys,
        audit=audit,
        redis=redis,
    )
    return None
