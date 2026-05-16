"""Integration: janitor sweep re-queues stale classifier docs exactly once.

Asserts AGENTS.md §4 rule #4 mitigation: the janitor finds Cosmos docs with
`classifier_status='queued'` older than the staleness threshold and pushes
their id back onto the `queue:classifier` Redis LIST, writing one `classify`
audit row per re-queue with `metadata.requeued_by='janitor'`.

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
from backend.core.redis import key_queue_classifier
from backend.services.janitor import janitor_classifier_queue
from backend.services.skill_bundle import slugify

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _seed_queued_skill(settings, *, skill_id: str, uploaded_at: str) -> str:
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
        skills = db.get_container_client("skills")
        doc_id = f"{skill_id}::1.0.0"
        await skills.upsert_item(
            body={
                "id": doc_id,
                "skill_id": skill_id,
                "version": "1.0.0",
                "name": skill_id,
                "description": "janitor sweep test",
                "uploader": "alice@example.com",
                "status": "pending",
                "classifier_status": "queued",
                "pinned": False,
                "uploaded_at": uploaded_at,
                "approved_at": None,
                "usage": {
                    "load_count": 0,
                    "last_loaded_at": None,
                    "loaders_30d": 0,
                },
            }
        )
        return doc_id
    finally:
        await cosmos.close()


async def _cleanup(settings, *, skill_ids: list[str]) -> None:
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
        for sid in skill_ids:
            for name in ("skills", "audit"):
                cont = db.get_container_client(name)
                with contextlib.suppress(Exception):
                    async for row in cont.query_items(
                        query="SELECT c.id, c.skill_id FROM c WHERE c.skill_id=@id",
                        parameters=[{"name": "@id", "value": sid}],
                        partition_key=sid,
                    ):
                        with contextlib.suppress(Exception):
                            await cont.delete_item(item=row["id"], partition_key=sid)
    finally:
        await cosmos.close()


async def _drain_queue(redis) -> int:
    count = 0
    while True:
        m = await redis.lpop(key_queue_classifier())
        if m is None:
            break
        count += 1
    return count


async def test_janitor_requeues_stale_docs_exactly_once(app_client):
    client, app = app_client  # noqa: F841 — client unused but fixture keeps lifespan
    settings = get_settings()

    stale_seconds = (
        settings.classifier_blpop_timeout_seconds * settings.janitor_classifier_stale_multiplier
    )
    now = datetime.now(UTC)
    uploaded_at = (now - timedelta(seconds=stale_seconds + 60)).isoformat()

    skill_ids = [slugify(f"janitor-sweep-{i}") for i in range(3)]
    doc_ids: list[str] = []

    redis = app.state.redis
    db = app.state.cosmos_db
    skills_container = db.get_container_client("skills")
    audit_container = db.get_container_client("audit")

    try:
        for sid in skill_ids:
            doc_ids.append(
                await _seed_queued_skill(settings, skill_id=sid, uploaded_at=uploaded_at)
            )

        # Drain to a known-empty state.
        await _drain_queue(redis)
        assert await redis.llen(key_queue_classifier()) == 0

        # --- First sweep ---
        result = await janitor_classifier_queue(
            skills=skills_container,
            audit=audit_container,
            redis=redis,
            settings=settings,
            now=now,
        )
        assert result == {"scanned": 3, "requeued": 3}
        assert await redis.llen(key_queue_classifier()) == 3

        popped: list[str] = []
        while True:
            m = await redis.lpop(key_queue_classifier())
            if m is None:
                break
            popped.append(m if isinstance(m, str) else m.decode())
        assert len(popped) == 3
        assert set(popped) == set(doc_ids)

        # Audit: exactly one classify row per skill with requeued_by=janitor.
        for sid in skill_ids:
            rows: list[dict] = []
            async for r in audit_container.query_items(
                query=("SELECT * FROM c WHERE c.skill_id=@id AND c.action='classify'"),
                parameters=[{"name": "@id", "value": sid}],
                partition_key=sid,
            ):
                rows.append(r)
            janitor_rows = [
                r for r in rows if (r.get("metadata") or {}).get("requeued_by") == "janitor"
            ]
            assert len(janitor_rows) == 1, sid

        # --- Idempotence sub-assertion ---
        # Janitor doesn't mutate doc status; re-runs at the same `now` re-queue
        # the same docs. Production mitigation against runaway re-queueing is
        # the classifier worker draining the queue faster than the janitor cron.
        result2 = await janitor_classifier_queue(
            skills=skills_container,
            audit=audit_container,
            redis=redis,
            settings=settings,
            now=now,
        )
        assert result2 == {"scanned": 3, "requeued": 3}
        assert await redis.llen(key_queue_classifier()) == 3
    finally:
        with contextlib.suppress(Exception):
            await _drain_queue(redis)
        await _cleanup(settings, skill_ids=skill_ids)
