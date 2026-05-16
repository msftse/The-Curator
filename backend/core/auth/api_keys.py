"""API key issue/resolve/revoke.

Cosmos-first writes (AGENTS.md §4 rule #1). Redis is a 60s read-through
cache only (rule #2 + rule #3). Revoke is a soft flag (`revoked_at`) so
the audit trail survives — never a delete (AGENTS.md §5 by analogy).

Storage:
- Container `api_keys`, PK `/key_id`.
- We persist the SHA-256 hex of `pepper + raw_key`. The raw key never
  touches storage and is returned to the caller exactly once at issue
  time.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from azure.cosmos.aio import ContainerProxy
from redis.asyncio import Redis

from backend.core.auth.models import Scope, ServiceAccount
from backend.core.config import Settings
from backend.core.errors import InvalidToken, RevokedApiKey
from backend.models.api_key import ApiKeyDoc


def _hash(raw: str, pepper: str) -> str:
    return hashlib.sha256((pepper + raw).encode("utf-8")).hexdigest()


def _cache_key(hash_hex: str) -> str:
    # We only put the first 16 chars of the hash in the cache key so a Redis
    # dump never recovers a usable token.
    return f"cache:apikey:{hash_hex[:16]}"


def generate_raw_key(prefix: str = "sh_live_") -> str:
    """Mint a fresh opaque token. ~32 bytes of entropy after the prefix."""
    return f"{prefix}{secrets.token_urlsafe(32)}"


async def issue(
    *,
    name: str,
    scopes: list[Scope],
    actor: str,
    api_keys: ContainerProxy,
    settings: Settings,
) -> tuple[ApiKeyDoc, str]:
    """Mint a new key. Returns (doc, raw_key). Raw key is returned exactly once."""
    raw = generate_raw_key(settings.apikey_prefix)
    key_id = uuid.uuid4().hex
    doc = ApiKeyDoc(
        id=key_id,
        key_id=key_id,
        name=name,
        scopes=scopes,
        hash_sha256=_hash(raw, settings.apikey_pepper),
        created_by=actor,
        created_at=datetime.now(UTC),
        revoked_at=None,
        last_used_at=None,
    )
    await api_keys.create_item(body=doc.model_dump(mode="json"))
    return doc, raw


async def revoke(
    *,
    key_id: str,
    actor: str,
    api_keys: ContainerProxy,
    redis: Redis | None = None,
) -> ApiKeyDoc:
    raw = await api_keys.read_item(item=key_id, partition_key=key_id)
    doc = ApiKeyDoc.model_validate(raw)
    if doc.revoked_at is not None:
        return doc
    doc.revoked_at = datetime.now(UTC)
    await api_keys.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
    if redis is not None:
        with contextlib.suppress(Exception):
            await redis.delete(_cache_key(doc.hash_sha256))
    return doc


async def _query_by_hash(api_keys: ContainerProxy, hash_hex: str) -> dict[str, Any] | None:
    query = "SELECT * FROM c WHERE c.hash_sha256 = @h"
    params = [{"name": "@h", "value": hash_hex}]
    # We have to scan because the hash isn't the partition key (`key_id` is) —
    # the alternative would be a second container partitioned by hash, but
    # then revocation needs a two-phase write. M1 trade-off: small N, cache
    # absorbs the load.
    async for item in api_keys.query_items(
        query=query, parameters=params
    ):
        return item
    return None


async def resolve_api_key(
    raw_token: str,
    *,
    api_keys: ContainerProxy,
    redis: Redis | None,
    settings: Settings,
) -> ServiceAccount:
    """Resolve a `Authorization: Bearer sh_live_...` token to a ServiceAccount.

    Order of operations (per AGENTS.md §4):
    1. Redis cache lookup (with Cosmos fallback — rule #2).
    2. Cosmos query.
    3. 60s cache populate with TTL (rule #3).
    4. Fire-and-forget `last_used_at` update.
    """
    if not raw_token.startswith(settings.apikey_prefix):
        raise InvalidToken("token does not look like an API key")
    hash_hex = _hash(raw_token, settings.apikey_pepper)
    cache_key = _cache_key(hash_hex)

    cached: str | None = None
    if redis is not None:
        with contextlib.suppress(Exception):
            cached = await redis.get(cache_key)

    if cached == "REVOKED":
        raise RevokedApiKey("api key has been revoked")
    if cached and cached != "REVOKED":
        # Cache value is the JSON-encoded ServiceAccount.
        return ServiceAccount.model_validate_json(cached)

    item = await _query_by_hash(api_keys, hash_hex)
    if item is None:
        raise InvalidToken("api key not found")
    doc = ApiKeyDoc.model_validate(item)
    if doc.revoked_at is not None:
        if redis is not None:
            with contextlib.suppress(Exception):
                await redis.setex(cache_key, settings.apikey_cache_ttl_seconds, "REVOKED")
        raise RevokedApiKey("api key has been revoked")
    sa = ServiceAccount(service_account_id=doc.key_id, name=doc.name, scopes=list(doc.scopes))
    if redis is not None:
        with contextlib.suppress(Exception):
            await redis.setex(
                cache_key,
                settings.apikey_cache_ttl_seconds,
                sa.model_dump_json(),
            )
    # Fire-and-forget last_used_at update — never block the caller on it.
    asyncio.create_task(_touch_last_used(api_keys, doc))
    return sa


async def _touch_last_used(api_keys: ContainerProxy, doc: ApiKeyDoc) -> None:
    with contextlib.suppress(Exception):
        doc.last_used_at = datetime.now(UTC)
        await api_keys.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
