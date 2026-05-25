"""Integration: classifier worker hands off to defender worker (M5-2).

Asserts the end-to-end queue flow:

    upload → queue:classifier → classifier.process_one → queue:defender
           → defender.process_one (fake provider) → Cosmos `defender_status`

Skipped automatically when the emulator stack isn't running. Uses the
fake defender provider (settings.defender_provider="fake" via direct
scanner injection) so this test doesn't require Foundry credentials.
"""

from __future__ import annotations

import contextlib

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
    key_queue_defender,
    key_queue_notifications,
)
from backend.services.defender.scanner import FakeDefenderScanner
from backend.services.skill_bundle import slugify
from backend.workers.classifier import process_one as classifier_process_one
from backend.workers.defender import process_one as defender_process_one

pytestmark = pytest.mark.integration


SKILL_MD = """---
name: defender-flow-skill
description: integration test for classifier→defender handoff
category: devops
tags: [defender, e2e]
---
# defender-flow-skill

Drives the M5-2 classifier→defender queue handoff integration test.
"""


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _cleanup(settings) -> None:
    skill_id = slugify("defender-flow-skill")
    client = get_cosmos_client(settings)
    try:
        db = client.get_database_client(settings.cosmos_db_name)
        for cname in ("skills", "audit"):
            cont = db.get_container_client(cname)
            async for row in cont.query_items(
                query="SELECT c.id FROM c WHERE c.skill_id=@id",
                parameters=[{"name": "@id", "value": skill_id}],
                partition_key=skill_id,
            ):
                with contextlib.suppress(Exception):
                    await cont.delete_item(item=row["id"], partition_key=skill_id)
    finally:
        await client.close()
    r = get_redis(settings)
    try:
        await r.delete(
            key_cache_list(),
            key_cache_item(skill_id),
            key_queue_classifier(),
            key_queue_defender(),
            key_queue_notifications(),
        )
    finally:
        await r.aclose()


async def test_classifier_to_defender_handoff(app_client):
    client, app = app_client
    settings = get_settings()
    await _cleanup(settings)

    # 1. Upload.
    files = {"file": ("SKILL.md", SKILL_MD.encode(), "text/markdown")}
    resp = await client.post("/v1/uploads", files=files, headers={"X-User-Email": "alice@org"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    skill_id = body["skill_id"]

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
    # New M5-2 default — defender_status starts at pending.
    assert rows[0].get("defender_status", "pending") == "pending"

    # 2. Drive one classifier tick. This must rpush to queue:defender.
    redis = app.state.redis
    msg = await redis.blpop([key_queue_classifier()], timeout=5)
    assert msg is not None
    cosmos_client = app.state.cosmos_client
    await classifier_process_one(
        doc_id=doc_id,
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
    )

    # 3. The defender queue must now hold this doc id.
    assert await redis.llen(key_queue_defender()) == 1
    msg = await redis.blpop([key_queue_defender()], timeout=5)
    assert msg is not None
    _key, popped = msg
    assert popped == doc_id

    # 4. Drive one defender tick with the in-process fake scanner. We do
    # NOT spin the long-running loop — `process_one` lets us run exactly
    # one job and inspect the result deterministically.
    await defender_process_one(
        doc_id=doc_id,
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
        scanner=FakeDefenderScanner(),  # default = clean
    )

    after = await skills.read_item(item=doc_id, partition_key=skill_id)
    assert after["defender_status"] == "clean"
    assert after["defender_severity"] == "clean"
    assert after["defender_report"] is not None
    assert after["defender_scanned_at"] is not None

    # 5. Notifier placeholder push happened.
    assert await redis.llen(key_queue_notifications()) >= 1

    await _cleanup(settings)
