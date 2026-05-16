"""Catalog detail + human-usage endpoints (M2.3).

Verifies:
- `GET /v1/skills/{id}` returns `SkillDetail` with `skill_md_text` populated
  from the Cosmos doc (catalog list intentionally omits this field).
- `POST /v1/skills/{id}/usage` accepts a `web-ui:<email>` loader_id from a
  signed-in human (no `usage:write` scope required for User principals),
  writes a row to the `usage_events` container, and bumps the per-skill
  `loaders_30d` counter on the `skills` doc.

Reuses the e2e bootstrap (upload → classify → approve) so we exercise the
real publish path that writes `skill_md_text` onto the approved doc.
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
)
from backend.services.skill_bundle import slugify
from backend.workers.classifier import process_one

pytestmark = pytest.mark.integration


SKILL_NAME = "catalog-detail-skill"
SKILL_MD = f"""---
name: {SKILL_NAME}
description: catalog detail endpoint coverage
category: testing
tags: [catalog, detail]
---
# {SKILL_NAME}

Body content the frontend renders via MarkdownView.
"""


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _cleanup(settings) -> None:
    skill_id = slugify(SKILL_NAME)
    client = get_cosmos_client(settings)
    try:
        db = client.get_database_client(settings.cosmos_db_name)
        for name in ("skills", "audit", "usage_events"):
            cont = db.get_container_client(name)
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


async def _bootstrap_approved_skill(client: httpx.AsyncClient, app) -> str:
    files = {"file": ("SKILL.md", SKILL_MD.encode(), "text/markdown")}
    resp = await client.post(
        "/v1/uploads",
        files=files,
        headers={"X-User-Email": "alice@org"},
    )
    assert resp.status_code == 201, resp.text
    skill_id = resp.json()["skill_id"]

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
    doc_id = rows[0]["id"]

    redis = app.state.redis
    msg = await redis.blpop([key_queue_classifier()], timeout=5)
    assert msg is not None
    await process_one(
        doc_id=doc_id,
        cosmos_client=app.state.cosmos_client,
        redis=redis,
        settings=get_settings(),
    )

    resp = await client.post(
        f"/v1/admin/skills/{skill_id}/approve",
        headers={"X-User-Email": "manager@org"},
    )
    assert resp.status_code == 200, resp.text
    return skill_id


async def test_catalog_detail_returns_skill_md_text(app_client):
    client, app = app_client
    settings = get_settings()
    await _cleanup(settings)
    try:
        skill_id = await _bootstrap_approved_skill(client, app)

        # List endpoint must NOT carry skill_md_text — keeps catalog list lean.
        resp = await client.get("/v1/skills", headers={"X-User-Email": "consumer@org"})
        assert resp.status_code == 200
        listed = next(s for s in resp.json() if s["skill_id"] == skill_id)
        assert "skill_md_text" not in listed

        # Detail endpoint carries the rendered SKILL.md body.
        resp = await client.get(
            f"/v1/skills/{skill_id}",
            headers={"X-User-Email": "consumer@org"},
        )
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        assert detail["skill_id"] == skill_id
        assert detail["status"] == "approved"
        assert detail["skill_md_text"].startswith("---")
        assert f"# {SKILL_NAME}" in detail["skill_md_text"]
        assert "Body content the frontend renders" in detail["skill_md_text"]
    finally:
        await _cleanup(settings)


async def test_human_usage_event_records_to_cosmos(app_client):
    client, app = app_client
    settings = get_settings()
    await _cleanup(settings)
    try:
        skill_id = await _bootstrap_approved_skill(client, app)

        # Human download path: web UI posts a usage event with a `web-ui:` loader.
        resp = await client.post(
            f"/v1/skills/{skill_id}/usage",
            json={"loader_id": "web-ui:consumer@org"},
            headers={"X-User-Email": "consumer@org"},
        )
        assert resp.status_code == 200, resp.text
        event = resp.json()
        assert event["skill_id"] == skill_id
        assert event["loader_id"] == "web-ui:consumer@org"

        # usage_events container has exactly one row for this skill.
        db = app.state.cosmos_db
        usage = db.get_container_client("usage_events")
        rows = [
            r
            async for r in usage.query_items(
                query="SELECT * FROM c WHERE c.skill_id=@id",
                parameters=[{"name": "@id", "value": skill_id}],
                partition_key=skill_id,
            )
        ]
        assert len(rows) == 1
        assert rows[0]["loader_id"] == "web-ui:consumer@org"

        # The skill doc's loaders_30d counter reflects the new event.
        skills = db.get_container_client("skills")
        skill_rows = [
            r
            async for r in skills.query_items(
                query="SELECT * FROM c WHERE c.skill_id=@id",
                parameters=[{"name": "@id", "value": skill_id}],
                partition_key=skill_id,
            )
        ]
        assert len(skill_rows) == 1
        assert skill_rows[0]["usage"]["loaders_30d"] >= 1
    finally:
        await _cleanup(settings)
