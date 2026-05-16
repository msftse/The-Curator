"""Redis lock acquire/release + contention."""

from __future__ import annotations

import pytest

from backend.core.config import get_settings
from backend.core.errors import LockUnavailable
from backend.core.redis import get_redis, redis_lock

pytestmark = pytest.mark.integration


async def test_lock_acquires_and_releases():
    r = get_redis(get_settings())
    try:
        async with redis_lock(r, "test:lock:basic", ttl=5):
            assert await r.get("test:lock:basic") is not None
        # Released.
        assert await r.get("test:lock:basic") is None
    finally:
        await r.aclose()


async def test_lock_contention_raises():
    r = get_redis(get_settings())
    try:
        async with redis_lock(r, "test:lock:contended", ttl=5):
            with pytest.raises(LockUnavailable):
                async with redis_lock(r, "test:lock:contended", ttl=5):
                    pass
    finally:
        await r.aclose()


async def test_aof_enabled_smoke():
    """Rule #4 mitigation: classifier queue is on AOF Redis."""
    r = get_redis(get_settings())
    try:
        val = await r.config_get("appendonly")
        assert val.get("appendonly") == "yes"
    finally:
        await r.aclose()
