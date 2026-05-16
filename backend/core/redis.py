"""Async Redis client + distributed-lock helper.

Redis is cache + ephemeral coordination ONLY. Every read path in this codebase
must have a Cosmos fallback (AGENTS.md §4 rule #2). Every key must have a TTL
(rule #3). The classifier queue is the only ephemeral-data exception (rule #4).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

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


def get_redis(settings: Settings) -> Redis:
    """Build an async Redis client. `decode_responses=True` for ergonomic str API."""
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
