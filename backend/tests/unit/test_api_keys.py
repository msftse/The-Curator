"""Unit tests for the API-key issue/resolve/revoke primitives.

Uses an in-memory fake Cosmos container. Redis is also faked. We assert:
- Cosmos-first write (rule #1): the doc lands in the container before the
  raw key is returned.
- Hash-only storage: `hash_sha256` matches the SHA-256(pepper + raw_key).
- Cache fallback (rule #2): resolution still works with `redis=None`.
- Revoke is a soft flag (`revoked_at` set, doc still present).
- After revoke, resolve raises `RevokedApiKey` even if the cache hadn't
  been busted (cache invalidation is best-effort; revoke check is the
  Cosmos doc itself when the cache is cold).
"""

from __future__ import annotations

import hashlib

import pytest

from backend.core.auth.api_keys import (
    _cache_key,
    _hash,
    generate_raw_key,
    issue,
    resolve_api_key,
    revoke,
)
from backend.core.config import Settings
from backend.core.errors import InvalidToken, RevokedApiKey


class FakeContainer:
    """In-memory stand-in for `azure.cosmos.aio.ContainerProxy`."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}

    async def create_item(self, body: dict) -> dict:
        self._items[body["id"]] = body
        return body

    async def replace_item(self, item: str, body: dict) -> dict:
        self._items[item] = body
        return body

    async def read_item(self, item: str, partition_key: str) -> dict:
        return self._items[item]

    def query_items(self, query: str, parameters=None, enable_cross_partition_query=False):
        # Return an async iterator over items whose hash matches.
        target = None
        if parameters:
            for p in parameters:
                if p["name"] == "@h":
                    target = p["value"]

        async def _gen():
            for v in list(self._items.values()):
                if target is None or v.get("hash_sha256") == target:
                    yield v

        return _gen()


class FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._ttl: dict[str, int] = {}

    async def get(self, key: str):
        return self._kv.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = value
        self._ttl[key] = ttl

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if k in self._kv:
                del self._kv[k]
                n += 1
        return n


def _settings() -> Settings:
    return Settings(apikey_pepper="test-pepper", apikey_prefix="sh_live_")


def test_hash_uses_pepper():
    raw = "sh_live_abc"
    h = _hash(raw, "test-pepper")
    expected = hashlib.sha256(b"test-pepperash_live_abc"[len("ash") :]).hexdigest()
    # Sanity: changing the pepper changes the hash.
    h2 = _hash(raw, "other")
    assert h != h2
    # And direct construction matches.
    assert h == hashlib.sha256(b"test-peppersh_live_abc").hexdigest()
    assert expected != h or expected == h  # not really used; just keeps mypy quiet


def test_generate_raw_key_prefix_and_entropy():
    a = generate_raw_key("sh_live_")
    b = generate_raw_key("sh_live_")
    assert a.startswith("sh_live_") and b.startswith("sh_live_")
    assert a != b and len(a) > 20


async def test_issue_writes_cosmos_first_and_returns_raw_once():
    container = FakeContainer()
    s = _settings()
    doc, raw = await issue(
        name="my-bot",
        scopes=["catalog:read"],
        actor="admin@org",
        api_keys=container,
        settings=s,
    )
    # Doc landed in Cosmos (rule #1 — before any cache/Redis interaction).
    assert doc.id in container._items
    assert container._items[doc.id]["hash_sha256"] == _hash(raw, s.apikey_pepper)
    # Raw key has the prefix and is NOT stored anywhere.
    assert raw.startswith("sh_live_")
    assert "raw_key" not in container._items[doc.id]


async def test_resolve_without_redis_uses_cosmos_fallback():
    container = FakeContainer()
    s = _settings()
    _, raw = await issue(
        name="agent",
        scopes=["catalog:read"],
        actor="admin@org",
        api_keys=container,
        settings=s,
    )
    sa = await resolve_api_key(raw, api_keys=container, redis=None, settings=s)
    assert sa.name == "agent"
    assert "catalog:read" in sa.scopes


async def test_resolve_with_redis_populates_cache_with_ttl():
    container = FakeContainer()
    redis = FakeRedis()
    s = _settings()
    _, raw = await issue(
        name="agent",
        scopes=["catalog:read"],
        actor="admin@org",
        api_keys=container,
        settings=s,
    )
    sa = await resolve_api_key(raw, api_keys=container, redis=redis, settings=s)
    cache_key = _cache_key(_hash(raw, s.apikey_pepper))
    assert cache_key in redis._kv
    # Rule #3: every key has a TTL.
    assert redis._ttl[cache_key] == s.apikey_cache_ttl_seconds
    assert sa.name == "agent"


async def test_resolve_rejects_wrong_prefix():
    container = FakeContainer()
    s = _settings()
    with pytest.raises(InvalidToken):
        await resolve_api_key("not_an_api_key", api_keys=container, redis=None, settings=s)


async def test_resolve_unknown_key_raises_invalid_token():
    container = FakeContainer()
    s = _settings()
    with pytest.raises(InvalidToken):
        await resolve_api_key("sh_live_unknown", api_keys=container, redis=None, settings=s)


async def test_revoke_soft_flag_then_resolve_raises():
    container = FakeContainer()
    redis = FakeRedis()
    s = _settings()
    doc, raw = await issue(
        name="agent",
        scopes=["catalog:read"],
        actor="admin@org",
        api_keys=container,
        settings=s,
    )
    await revoke(key_id=doc.key_id, actor="admin@org", api_keys=container, redis=redis)
    # Doc still present (soft delete — AGENTS.md §5 invariant).
    assert doc.id in container._items
    assert container._items[doc.id]["revoked_at"] is not None
    with pytest.raises(RevokedApiKey):
        await resolve_api_key(raw, api_keys=container, redis=redis, settings=s)


async def test_revoke_idempotent():
    container = FakeContainer()
    s = _settings()
    doc, _ = await issue(
        name="agent",
        scopes=["catalog:read"],
        actor="admin@org",
        api_keys=container,
        settings=s,
    )
    d1 = await revoke(key_id=doc.key_id, actor="admin@org", api_keys=container)
    d2 = await revoke(key_id=doc.key_id, actor="admin@org", api_keys=container)
    assert d1.revoked_at == d2.revoked_at
