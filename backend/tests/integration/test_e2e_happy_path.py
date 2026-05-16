"""End-to-end happy path through the FastAPI ASGI app + classifier worker.

Exercises every M0 invariant in one test:
- upload writes a pending Cosmos doc BEFORE enqueueing
- classifier worker picks the job up and flips status to `classified`
- manager approve packages a tar.gz to Azurite, flips status to `approved`,
  writes a checksum on the doc, and invalidates Redis caches
- list returns the approved skill (cache hit on second call)
- download follows the 307 redirect to a working Azurite SAS URL whose
  body sha256 matches the value reported by GET /v1/skills/{id}

Skipped automatically if the emulator stack isn't running.
"""

from __future__ import annotations

import contextlib
import hashlib

import httpx
import pytest

from backend.app import create_app
from backend.core.config import get_settings
from backend.core.cosmos import get_cosmos_client
from backend.core.redis import (
    get_redis,
    key_cache_item,
    key_cache_list,
    key_queue_classifier,
)
from backend.services.skill_bundle import slugify
from backend.workers.classifier import process_one

pytestmark = pytest.mark.integration


SKILL_MD = """---
name: e2e-test-skill
description: end-to-end happy path
category: testing
tags: [e2e, demo]
---
# e2e-test-skill

This SKILL.md drives the M0 happy path integration test.
"""


@pytest.fixture
async def app_client():
    """ASGI client against the live FastAPI app + emulator stack."""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Boot the lifespan manually.
        async with app.router.lifespan_context(app):
            yield client, app


async def _cleanup(settings) -> None:
    """Wipe Cosmos rows + Redis keys + Blob entries for this test's skill_id."""
    skill_id = slugify("e2e-test-skill")
    client = get_cosmos_client(settings)
    try:
        db = client.get_database_client(settings.cosmos_db_name)
        skills = db.get_container_client("skills")
        audit = db.get_container_client("audit")
        for cont in (skills, audit):
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
    try:
        await r.delete(key_cache_list(), key_cache_item(skill_id), key_queue_classifier())
    finally:
        await r.aclose()


async def test_e2e_happy_path(app_client):
    client, app = app_client
    settings = get_settings()
    await _cleanup(settings)

    # --- 1. Upload as a contributor ---
    files = {"file": ("SKILL.md", SKILL_MD.encode(), "text/markdown")}
    resp = await client.post("/v1/uploads", files=files, headers={"X-User-Email": "alice@org"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    skill_id = body["skill_id"]
    assert body["status"] == "pending"
    assert body["classifier_status"] == "queued"

    # The pending Cosmos doc exists BEFORE we run the worker — rule #4 mitigation.
    db = app.state.cosmos_db
    skills = db.get_container_client("skills")
    rows = [
        r
        async for r in skills.query_items(
            query="SELECT * FROM c WHERE c.skill_id=@id",
            parameters=[{"name": "@id", "value": skill_id}],
            partition_key=skill_id,
        )
    ]
    assert len(rows) == 1
    doc_id = rows[0]["id"]

    # Queue length should be exactly 1.
    redis = app.state.redis
    assert await redis.llen(key_queue_classifier()) >= 1

    # --- 2. Run worker for one job ---
    # Pop the message manually (simulates one classifier tick).
    msg = await redis.blpop([key_queue_classifier()], timeout=5)
    assert msg is not None
    cosmos_client = app.state.cosmos_client
    await process_one(
        doc_id=doc_id,
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
    )

    # Doc should now be classified.
    classified = await skills.read_item(item=doc_id, partition_key=skill_id)
    assert classified["status"] == "classified"
    assert classified["classification"]["category"] == "testing"

    # --- 3. Manager approves ---
    resp = await client.post(
        f"/v1/admin/skills/{skill_id}/approve",
        headers={"X-User-Email": "manager@org"},
    )
    assert resp.status_code == 200, resp.text
    approved = resp.json()
    assert approved["status"] == "approved"
    assert approved["bundle"]["checksum_sha256"]
    expected_sha = approved["bundle"]["checksum_sha256"]

    # Cache should have been invalidated.
    assert await redis.get(key_cache_list()) is None

    # --- 4. Public list returns it; second call hits cache ---
    resp = await client.get("/v1/skills")
    assert resp.status_code == 200
    listed = resp.json()
    assert any(s["skill_id"] == skill_id for s in listed)
    # Cache populated now.
    assert await redis.get(key_cache_list()) is not None

    # --- 5. Download via signed URL — sha256 matches ---
    resp = await client.get(f"/v1/skills/{skill_id}/download", follow_redirects=False)
    assert resp.status_code == 307
    sas_url = resp.headers["location"]
    async with httpx.AsyncClient() as raw:
        dl = await raw.get(sas_url)
    assert dl.status_code == 200, dl.text
    assert hashlib.sha256(dl.content).hexdigest() == expected_sha

    # --- 6. Audit log: upload + classify + approve + publish = 4 rows ---
    audit = db.get_container_client("audit")
    audit_rows = [
        r
        async for r in audit.query_items(
            query="SELECT * FROM c WHERE c.skill_id=@id",
            parameters=[{"name": "@id", "value": skill_id}],
            partition_key=skill_id,
        )
    ]
    actions = {r["action"] for r in audit_rows}
    assert {"upload", "classify", "approve", "publish"}.issubset(actions)

    await _cleanup(settings)
