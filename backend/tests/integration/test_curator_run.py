"""Integration: curator pass + snapshot + dry-run vs real diff.

Requires emulator stack. Skipped automatically otherwise.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.app import create_app
from backend.core.config import get_settings
from backend.core.cosmos import get_cosmos_client
from backend.services.skill_bundle import slugify

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _seed_old_skill(settings, *, skill_id: str, days_old: int) -> None:
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
        skills = db.get_container_client("skills")
        uploaded = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
        await skills.upsert_item(
            body={
                "id": f"{skill_id}::1.0.0",
                "skill_id": skill_id,
                "version": "1.0.0",
                "name": skill_id,
                "description": "x",
                "uploader": "alice@example.com",
                "status": "approved",
                "classifier_status": "complete",
                "pinned": False,
                "uploaded_at": uploaded,
                "approved_at": uploaded,
                "usage": {
                    "load_count": 0,
                    "last_loaded_at": None,
                    "loaders_30d": 0,
                },
            }
        )
    finally:
        await cosmos.close()


async def _cleanup(settings, skill_id: str) -> None:
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
        for name in ("skills", "audit"):
            cont = db.get_container_client(name)
            with contextlib.suppress(Exception):
                async for row in cont.query_items(
                    query="SELECT c.id, c.skill_id FROM c WHERE c.skill_id=@id",
                    parameters=[{"name": "@id", "value": skill_id}],
                    partition_key=skill_id,
                ):
                    with contextlib.suppress(Exception):
                        await cont.delete_item(
                            item=row["id"], partition_key=skill_id
                        )
    finally:
        await cosmos.close()


async def test_dry_run_then_real_match(app_client, as_admin):
    client, app = app_client
    settings = get_settings()
    skill_id = slugify("curator-it-test")

    await _seed_old_skill(settings, skill_id=skill_id, days_old=200)
    try:
        as_admin(app, email="admin@example.com")

        # Dry-run first
        r1 = await client.post("/v1/admin/curator/run?dry_run=true")
        assert r1.status_code == 200, r1.text
        dry = r1.json()
        dry_ids = sorted(t["skill_id"] for t in dry["transitions"])
        assert skill_id in dry_ids

        # Real run
        r2 = await client.post("/v1/admin/curator/run")
        assert r2.status_code == 200, r2.text
        real = r2.json()
        real_ids = sorted(
            t["skill_id"] for t in real["transitions"] if t["applied"]
        )
        # Real run should include at least the same skill_id.
        assert skill_id in real_ids
    finally:
        await _cleanup(settings, skill_id)
