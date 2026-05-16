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

import contextlib

from azure.cosmos.aio import ContainerProxy
from fastapi import Depends, Request
from redis.asyncio import Redis

from backend.core.auth.api_keys import resolve_api_key
from backend.core.auth.models import Principal, Role, Scope, ServiceAccount, User
from backend.core.auth.providers.base import IdentityProvider
from backend.core.config import Settings, get_settings
from backend.core.errors import Forbidden, MissingScope, Unauthorized
from backend.core.logging import get_logger

log = get_logger(__name__)

# First-seen-per-day admin audit. Redis SETNX with 24h TTL means at most one
# `admin_session_start` audit row per (oid, day). Email-keyed fallback when
# the user has no oid (stub mode). Failures are swallowed — the audit is a
# nice-to-have, never a hard request dependency.
_ADMIN_SESSION_TTL_SECONDS = 86400


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

    async def _checker(request: Request, user: User = Depends(get_current_user)) -> User:
        if not user.has_role(role):
            raise Forbidden(f"role {role!r} required")
        if role == "admin":
            await _maybe_record_admin_session(request, user)
        return user

    return _checker


async def _maybe_record_admin_session(request: Request, user: User) -> None:
    """Record at most one `admin_session_start` audit row per (oid, 24h).

    Uses Redis SETNX as a coordination primitive (rule #4: ephemeral, TTL'd,
    Cosmos has the durable audit row). Best-effort: any failure logs and
    returns. Skipped when the app isn't fully wired (e.g. unit tests).
    """
    redis: Redis | None = getattr(request.app.state, "redis", None)
    cosmos_db = getattr(request.app.state, "cosmos_db", None)
    if redis is None or cosmos_db is None:
        return

    # Key on `oid` when available (immutable), else email (mutable but stable
    # within a stub-mode dev session).
    actor_key = user.oid or user.email
    if not actor_key:
        return
    key = f"admin_seen:{actor_key}"
    try:
        won = await redis.set(key, "1", ex=_ADMIN_SESSION_TTL_SECONDS, nx=True)
    except Exception as exc:
        log.debug("admin_session_setnx_failed", extra={"err": str(exc)})
        return
    if not won:
        return  # Already audited within the last 24h.

    # Lazy import to avoid a top-level cycle (services → models → core.auth).
    try:
        from backend.core.cosmos import AUDIT_CONTAINER, get_container
        from backend.services import audit as audit_svc
    except Exception as exc:  # pragma: no cover — import safety net
        log.debug("admin_session_import_failed", extra={"err": str(exc)})
        return

    with contextlib.suppress(Exception):
        audit = get_container(cosmos_db, AUDIT_CONTAINER)
        await audit_svc.record(
            audit,
            skill_id="_system",
            action="admin_session_start",
            actor=user.email,
            actor_oid=user.oid,
            metadata={"roles": list(user.roles)},
        )


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
