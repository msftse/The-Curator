"""Redis-down fallback: GET /v1/skills still works when Redis is unreachable.

We simulate Redis being down by swapping `app.state.redis` for a sabotaged
client whose calls always raise. The Cosmos fallback in `catalog.list_approved`
should still return 200 (AGENTS.md §4 rule #2).
"""

from __future__ import annotations

import httpx
import pytest
from redis.exceptions import RedisError

from backend.app import create_app

pytestmark = pytest.mark.integration


class _BrokenRedis:
    """A fake redis client where every call raises."""

    async def get(self, *_a, **_k):
        raise RedisError("simulated down")

    async def set(self, *_a, **_k):
        raise RedisError("simulated down")

    async def delete(self, *_a, **_k):
        raise RedisError("simulated down")

    async def ping(self):
        raise RedisError("simulated down")

    async def aclose(self):
        pass


async def test_catalog_falls_back_to_cosmos_when_redis_down():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            # Sabotage redis post-lifespan.
            app.state.redis = _BrokenRedis()
            resp = await client.get("/v1/skills")
            assert resp.status_code == 200, resp.text
            # Body is a list (possibly empty) — degraded but not broken.
            assert isinstance(resp.json(), list)
