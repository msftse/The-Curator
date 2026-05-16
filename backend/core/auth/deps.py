"""Auth FastAPI dependencies.

`get_current_user` — humans only. Wraps the active `IdentityProvider`.
`get_principal` — humans OR machines. Dispatches on the `Authorization`
                  header prefix: `Bearer sh_live_...` → API key; anything
                  else → user provider.
`require_role`  — gate a route on a `User` having a role. Raises 403.
`require_scope` — gate a route on a `Principal` having a scope. ServiceAccount
                  scopes are checked directly; Users implicitly satisfy
                  every scope (admins-of-humans aren't scope-bound).
"""

from __future__ import annotations

from azure.cosmos.aio import ContainerProxy
from fastapi import Depends, Request
from redis.asyncio import Redis

from backend.core.auth.api_keys import resolve_api_key
from backend.core.auth.models import Principal, Role, Scope, ServiceAccount, User
from backend.core.auth.providers.base import IdentityProvider
from backend.core.config import Settings, get_settings
from backend.core.errors import Forbidden, MissingScope, Unauthorized


def get_identity_provider(request: Request) -> IdentityProvider:
    provider: IdentityProvider | None = getattr(request.app.state, "identity_provider", None)
    if provider is None:
        raise Unauthorized("identity provider not configured")
    return provider


async def get_current_user(request: Request) -> User:
    provider = get_identity_provider(request)
    return await provider.resolve(request)


async def get_principal(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Resolve a User or a ServiceAccount based on the auth header shape."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(None, 1)[1].strip()
        if token.startswith(settings.apikey_prefix):
            api_keys_container: ContainerProxy | None = getattr(
                request.app.state, "api_keys_container", None
            )
            if api_keys_container is None:
                raise Unauthorized("api_keys container not configured")
            redis: Redis | None = getattr(request.app.state, "redis", None)
            return await resolve_api_key(
                token,
                api_keys=api_keys_container,
                redis=redis,
                settings=settings,
            )
        # Bearer JWT → fall through to user provider.
    # Header-based or JWT-based human auth — let the provider decide.
    return await get_current_user(request)


def require_role(role: Role):
    """Build a dep that ensures the caller is a `User` with `role`."""

    async def _checker(user: User = Depends(get_current_user)) -> User:
        if not user.has_role(role):
            raise Forbidden(f"role {role!r} required")
        return user

    return _checker


def require_scope(scope: Scope):
    """Build a dep that ensures a Principal has `scope`.

    Users satisfy any scope (they're authenticated humans); ServiceAccounts
    must carry the scope explicitly.
    """

    async def _checker(principal: Principal = Depends(get_principal)) -> Principal:
        if isinstance(principal, ServiceAccount) and not principal.has_scope(scope):
            raise MissingScope(f"scope {scope!r} required")
        return principal

    return _checker
