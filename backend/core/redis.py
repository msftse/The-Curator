"""Async Redis client + distributed-lock helper.

Redis is cache + ephemeral coordination ONLY. Every read path in this codebase
must have a Cosmos fallback (AGENTS.md §4 rule #2). Every key must have a TTL
(rule #3). The classifier queue is the only ephemeral-data exception (rule #4).

Auth modes (selected by `Settings.redis_use_entra`):

* URL-based — credentials are embedded in `REDIS_URL` (works for Azurite
  locally and for Azure Cache for Redis access keys via `rediss://:KEY@host:6380/0`).
* Entra ID — `DefaultAzureCredential` mints a short-lived AAD token that is
  used as the Redis password. The token is refreshed automatically before
  expiry by the `_EntraTokenCredentialProvider` (see below).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from redis.credentials import CredentialProvider

from backend.core.config import Settings
from backend.core.errors import LockUnavailable

# Atomic compare-and-delete so we only release a lock we still own.
_UNLOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class _EntraTokenCredentialProvider(CredentialProvider):
    """Supplies a fresh AAD access token as the Redis password.

    redis-py invokes `get_credentials_async()` on every (re)connect and on
    auth re-issue. We cache the token and refresh when within 2 minutes of
    expiry — well inside Azure's ~60-min token lifetime.

    `DefaultAzureCredential` resolves the identity in this order: env vars,
    workload/managed identity, Azure CLI (`az login`), Azure Developer CLI,
    Azure PowerShell. The credential is lazily constructed so importing this
    module never triggers a network call.
    """

    _REFRESH_SKEW_SECONDS = 120

    def __init__(self, username: str, scope: str) -> None:
        self._username = username
        self._scope = scope
        self._credential = None  # type: ignore[assignment]
        self._cached_token: str | None = None
        self._expires_on: float = 0.0

    def _build_credential(self):  # noqa: ANN202 — azure-identity import is lazy
        from azure.identity.aio import DefaultAzureCredential

        return DefaultAzureCredential()

    async def _fetch_token(self) -> str:
        if self._credential is None:
            self._credential = self._build_credential()
        token = await self._credential.get_token(self._scope)
        self._cached_token = token.token
        self._expires_on = float(token.expires_on)
        return self._cached_token

    async def get_credentials_async(self) -> tuple[str, str]:
        now = time.time()
        if self._cached_token is None or now >= self._expires_on - self._REFRESH_SKEW_SECONDS:
            await self._fetch_token()
        assert self._cached_token is not None  # for type checkers
        return (self._username, self._cached_token)

    def get_credentials(self) -> tuple[str, str]:  # pragma: no cover — async path used
        raise RuntimeError(
            "Entra credential provider only supports async; the async Redis "
            "client should be calling get_credentials_async()."
        )

    async def aclose(self) -> None:
        if self._credential is not None:
            try:
                await self._credential.close()
            except Exception:
                pass


def get_redis(settings: Settings) -> Redis:
    """Build an async Redis client.

    `decode_responses=True` for an ergonomic str API. In Entra mode we
    construct the client with an AAD-backed `CredentialProvider`; otherwise
    we parse `REDIS_URL` (works for Azurite and for `rediss://:KEY@host` URLs
    against Azure Cache for Redis access keys).
    """
    if settings.redis_use_entra:
        if not settings.redis_host:
            raise RuntimeError(
                "REDIS_USE_ENTRA=true requires REDIS_HOST to be set "
                "(e.g. <name>.redis.cache.windows.net)."
            )
        if not settings.redis_entra_username:
            raise RuntimeError(
                "REDIS_USE_ENTRA=true requires REDIS_ENTRA_USERNAME "
                "(the object id of the principal granted a Redis data access policy)."
            )
        provider = _EntraTokenCredentialProvider(
            username=settings.redis_entra_username,
            scope=settings.redis_entra_scope,
        )
        return Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            ssl=settings.redis_ssl,
            credential_provider=provider,
            decode_responses=True,
        )
    return Redis.from_url(settings.redis_url, decode_responses=True)


# ----- Key helpers (single source of truth for key naming) -----


def key_cache_list() -> str:
    return "cache:skills:list:v1"


def key_cache_item(skill_id: str) -> str:
    return f"cache:skills:item:{skill_id}"


def key_queue_classifier() -> str:
    return "queue:classifier"


def key_lock_publish(skill_id: str) -> str:
    return f"lock:publish:{skill_id}"


def key_curator_run_lock() -> str:
    return "lock:curator:run"


def key_curator_pause() -> str:
    return "curator:paused"


# ----- Distributed lock -----


@asynccontextmanager
async def redis_lock(redis: Redis, key: str, ttl: int) -> AsyncIterator[str]:
    """Single-instance Redis lock: SET NX EX + atomic release.

    Good enough for M0/M1 on a single Redis instance. Not Redlock — that's M4
    if we ever care about multi-master. Raises `LockUnavailable` if another
    holder owns the key.
    """
    token = uuid.uuid4().hex
    acquired = await redis.set(key, token, nx=True, ex=ttl)
    if not acquired:
        raise LockUnavailable(f"could not acquire lock {key}")
    try:
        yield token
    finally:
        try:
            await redis.eval(_UNLOCK_LUA, 1, key, token)
        except Exception:
            # Best-effort release; the TTL is our safety net.
            pass
