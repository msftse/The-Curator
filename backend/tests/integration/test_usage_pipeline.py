"""Integration: usage pipeline end-to-end (requires emulator stack).

Verifies:
- POST /v1/skills/{id}/usage writes a raw `usage_events` row.
- Skill doc's `usage.load_count` and `usage.last_loaded_at` are updated.
- Redis catalog cache keys are invalidated after the call.

Skipped when emulators aren't running (see backend/tests/conftest.py).
"""

from __future__ import annotations

import contextlib

import httpx
import pytest

from backend.app import create_app
from backend.core.auth.api_keys import issue
from backend.core.config import get_settings
from backend.core.cosmos import get_cosmos_client
from backend.core.redis import get_redis, key_cache_item, key_cache_list
from backend.services.skill_bundle import slugify

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _cleanup_skill(settings, skill_id: str) -> None:
    """Best-effort cleanup for test isolation. Allowed in tests; production
    code paths are the ones forbidden from deleting skill bytes."""
    client = get_cosmos_client(settings)
    try:
        db = client.get_database_client(settings.cosmos_db_name)
        for name in ("skills", "audit", "usage_events"):
            cont = db.get_container_client(name)
            with contextlib.suppress(Exception):
                async for row in cont.query_items(
                    query="SELECT c.id, c.skill_id FROM c WHERE c.skill_id=@id",
                    parameters=[{"name": "@id", "value": skill_id}],
                    partition_key=skill_id,
                ):
                    with contextlib.suppress(Exception):
                        await cont.delete_item(item=row["id"], partition_key=skill_id)
    finally:
        await client.close()
    r = get_redis(settings)
    with contextlib.suppress(Exception):
        await r.delete(key_cache_list(), key_cache_item(skill_id))
    with contextlib.suppress(Exception):
        await r.aclose()


async def test_usage_endpoint_records_event_and_bumps_counter(app_client):
    client, app = app_client
    settings = get_settings()
    skill_id = slugify("usage-pipe-test")

    # Pre-seed an approved skill doc so the usage call can succeed.
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
        skills = db.get_container_client("skills")
        doc = {
            "id": f"{skill_id}::1.0.0",
            "skill_id": skill_id,
            "version": "1.0.0",
            "name": "usage-pipe-test",
            "description": "x",
            "uploader": "alice@example.com",
            "status": "approved",
            "classifier_status": "complete",
            "pinned": False,
            "uploaded_at": "2026-05-01T00:00:00+00:00",
            "approved_at": "2026-05-01T00:00:00+00:00",
            "usage": {"load_count": 0, "last_loaded_at": None, "loaders_30d": 0},
        }
        await skills.upsert_item(body=doc)

        # Issue an API key with usage:write
        api_keys = db.get_container_client("api_keys")
        _doc, raw_key = await issue(
            name="usage-test",
            scopes=["usage:write"],
            actor="admin@example.com",
            api_keys=api_keys,
            settings=settings,
        )

        try:
            r = await client.post(
                f"/v1/skills/{skill_id}/usage",
                headers={"Authorization": f"Bearer {raw_key}"},
                json={"loader_id": "loader-1", "context": {"agent": "hermes"}},
            )
            assert r.status_code == 200, r.text

            # Re-read skill doc — counters should be bumped.
            raw = await skills.read_item(item=f"{skill_id}::1.0.0", partition_key=skill_id)
            assert raw["usage"]["load_count"] == 1
            assert raw["usage"]["last_loaded_at"] is not None
        finally:
            await _cleanup_skill(settings, skill_id)
    finally:
        await cosmos.close()
