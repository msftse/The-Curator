"""End-to-end M5 full-flow tests (M5-8).

Two scenarios, both driving the real FastAPI ASGI app + classifier worker
+ defender worker + Redis + Cosmos + Azurite. Auto-skipped when the
local emulator stack isn't running (see backend/tests/conftest.py for
the port-based gate at 8081/10000/6379).

  (a) clean path:
        upload → classifier tick → defender tick (FakeDefenderScanner)
              → admin approve → status=approved + bundle in published/

  (b) flagged path:
        upload → classifier tick → defender tick (HIGH severity)
              → admin quarantine → status=quarantined, bytes in
                quarantine/, and a `skill.quarantined` NotificationEvent
                is on `queue:notifications`.

Notes on why both flows run against the real emulator stack:
- The point of the E2E gate is to catch any rewire that breaks the
  Redis queue handoff between workers — the unit tests stub Redis out.
- We use `process_one` on each worker rather than spinning the BLPOP
  loop so the test stays deterministic (no sleeps, no race windows).
- The notifier worker itself is NOT driven here; M5-5 already has an
  integration test for that side of the chain. We only assert the
  producer fired by checking the queue length.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import httpx
import pytest

# Fail-fast skip if the relevant SDKs aren't even importable. This is
# belt-and-suspenders on top of the port-based gate in conftest.py.
pytest.importorskip("azure.cosmos.aio")
pytest.importorskip("azure.storage.blob.aio")
pytest.importorskip("redis.asyncio")

from backend.app import create_app  # noqa: E402
from backend.core.blob import get_blob_service, quarantine_blob_path  # noqa: E402
from backend.core.config import get_settings  # noqa: E402
from backend.core.cosmos import get_cosmos_client  # noqa: E402
from backend.core.redis import (  # noqa: E402
    get_redis,
    key_cache_item,
    key_cache_list,
    key_queue_classifier,
    key_queue_defender,
    key_queue_notifications,
)
from backend.models.defender import (  # noqa: E402
    DefenderReport,
    DefenderSeverity,
    TokenUsage,
)
from backend.services.defender.scanner import FakeDefenderScanner  # noqa: E402
from backend.services.skill_bundle import slugify  # noqa: E402
from backend.workers.classifier import process_one as classifier_process_one  # noqa: E402
from backend.workers.defender import process_one as defender_process_one  # noqa: E402

# Reuse the same integration gate as the rest of the suite so the file
# is skipped cleanly when emulators are down.
pytestmark = pytest.mark.integration


SKILL_MD_CLEAN = """---
name: m5-e2e-clean-skill
description: M5-8 e2e clean path
category: devops
tags: [m5, e2e, clean]
---
# m5-e2e-clean-skill

Drives the M5-8 clean end-to-end test.
"""

SKILL_MD_FLAGGED = """---
name: m5-e2e-flagged-skill
description: M5-8 e2e flagged path
category: devops
tags: [m5, e2e, flagged]
---
# m5-e2e-flagged-skill

Drives the M5-8 flagged → quarantine end-to-end test.
"""


# --------------------------------------------------------------------------- #
# Fixtures (mirror existing integration tests so cleanup semantics match).
# --------------------------------------------------------------------------- #


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _cleanup(settings, skill_name: str) -> None:
    skill_id = slugify(skill_name)
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
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
        await cosmos.close()

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

    blob = get_blob_service(settings)
    try:
        for cname in (
            settings.blob_quarantine_container,
            settings.blob_published_container,
        ):
            cont = blob.get_container_client(cname)
            with contextlib.suppress(Exception):
                async for b in cont.list_blobs(name_starts_with=f"{skill_id}/"):
                    with contextlib.suppress(Exception):
                        await cont.get_blob_client(b.name).delete_blob()
    finally:
        await blob.close()


def _flagged_scanner() -> FakeDefenderScanner:
    return FakeDefenderScanner(
        [
            DefenderReport(
                overall_severity=DefenderSeverity.HIGH,
                findings=[],
                model="fake-v1",
                scanned_at=datetime.now(UTC),
                scan_duration_ms=5,
                token_usage=TokenUsage(input_tokens=50, output_tokens=10),
                notes="m5-8 e2e fixture: high severity",
            )
        ]
    )


# --------------------------------------------------------------------------- #
# Flow (a): upload → classifier → defender clean → admin approve → publish
# --------------------------------------------------------------------------- #


async def test_m5_full_flow_clean_then_approve(app_client, as_admin):
    client, app = app_client
    settings = get_settings()
    await _cleanup(settings, "m5-e2e-clean-skill")

    # 1. Upload as a contributor.
    files = {"file": ("SKILL.md", SKILL_MD_CLEAN.encode(), "text/markdown")}
    resp = await client.post(
        "/v1/uploads", files=files, headers={"X-User-Email": "alice@org"}
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
    assert len(rows) == 1
    doc_id = rows[0]["id"]

    redis = app.state.redis
    cosmos_client = app.state.cosmos_client

    # 2. Classifier tick.
    msg = await redis.blpop([key_queue_classifier()], timeout=5)
    assert msg is not None
    await classifier_process_one(
        doc_id=doc_id,
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
    )

    # 3. Defender tick — FakeDefenderScanner defaults to CLEAN.
    msg = await redis.blpop([key_queue_defender()], timeout=5)
    assert msg is not None
    _, popped = msg
    assert popped == doc_id

    await defender_process_one(
        doc_id=doc_id,
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
        scanner=FakeDefenderScanner(),
    )

    after = await skills.read_item(item=doc_id, partition_key=skill_id)
    assert after["defender_status"] == "clean"

    # 4. Admin approves → publish flow runs.
    as_admin(app, email="admin@example.com")
    try:
        ap = await client.post(f"/v1/admin/skills/{skill_id}/approve")
        assert ap.status_code == 200, ap.text
        body = ap.json()
        assert body["status"] == "approved"
        assert body["bundle"]["checksum_sha256"]
        expected_sha = body["bundle"]["checksum_sha256"]

        # 5. Bundle bytes are reachable via the public download redirect.
        dl = await client.get(
            f"/v1/skills/{skill_id}/download", follow_redirects=False
        )
        assert dl.status_code == 307
        sas_url = dl.headers["location"]
        async with httpx.AsyncClient() as raw:
            payload = await raw.get(sas_url)
        assert payload.status_code == 200

        import hashlib

        assert hashlib.sha256(payload.content).hexdigest() == expected_sha

        # 6. Audit chain — approve + publish rows exist.
        audit_c = db.get_container_client("audit")
        actions = {
            r["action"]
            async for r in audit_c.query_items(
                query="SELECT c.action FROM c WHERE c.skill_id=@id",
                parameters=[{"name": "@id", "value": skill_id}],
                partition_key=skill_id,
            )
        }
        assert {"upload", "approve", "publish"}.issubset(actions)

        # 7. Producer-side: `skill.approved` event landed on the queue.
        assert await redis.llen(key_queue_notifications()) >= 1
    finally:
        await _cleanup(settings, "m5-e2e-clean-skill")


# --------------------------------------------------------------------------- #
# Flow (b): upload → classifier → defender flagged → admin quarantine
#           → notifier `skill.quarantined` event emitted
# --------------------------------------------------------------------------- #


async def test_m5_full_flow_flagged_then_quarantine_emits_notifier_event(
    app_client, as_admin
):
    client, app = app_client
    settings = get_settings()
    await _cleanup(settings, "m5-e2e-flagged-skill")

    # 1. Upload.
    files = {"file": ("SKILL.md", SKILL_MD_FLAGGED.encode(), "text/markdown")}
    resp = await client.post(
        "/v1/uploads", files=files, headers={"X-User-Email": "alice@org"}
    )
    assert resp.status_code == 201, resp.text
    skill_id = resp.json()["skill_id"]

    db = app.state.cosmos_db
    skills = db.get_container_client("skills")
    audit_c = db.get_container_client("audit")
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

    redis = app.state.redis
    cosmos_client = app.state.cosmos_client

    # 2. Classifier tick.
    msg = await redis.blpop([key_queue_classifier()], timeout=5)
    assert msg is not None
    await classifier_process_one(
        doc_id=doc_id,
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
    )

    # 3. Defender tick with FLAGGED scanner.
    msg = await redis.blpop([key_queue_defender()], timeout=5)
    assert msg is not None
    await defender_process_one(
        doc_id=doc_id,
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
        scanner=_flagged_scanner(),
    )

    after = await skills.read_item(item=doc_id, partition_key=skill_id)
    assert after["defender_status"] == "flagged"
    assert after["defender_severity"] == "high"

    # Drain the `defender.flagged` notifier event from step 3 so we can
    # assert step 4 cleanly produced a fresh `skill.quarantined` event.
    drained_before_quarantine = 0
    while await redis.llen(key_queue_notifications()) > 0:
        m = await redis.blpop([key_queue_notifications()], timeout=1)
        if m is None:
            break
        drained_before_quarantine += 1
    # The defender-clean path emits 0 and the flagged path emits 1
    # (`defender.flagged`); we just sanity-check that we got at least one.
    assert drained_before_quarantine >= 1

    # 4. Admin quarantines.
    as_admin(app, email="admin@example.com")
    just = "bundle exfiltrates env vars via curl; high-severity defender finding"
    q = await client.post(
        f"/v1/admin/skills/{skill_id}/quarantine",
        json={"justification": just},
    )
    assert q.status_code == 200, q.text
    body = q.json()
    assert body["status"] == "quarantined"

    after2 = await skills.read_item(item=doc_id, partition_key=skill_id)
    assert after2["status"] == "quarantined"
    assert after2["quarantine_justification"] == just

    # 5. Bundle copied to quarantine/{id}/{ver}/bundle.tar.gz.
    blob = get_blob_service(settings)
    try:
        q_blob = blob.get_container_client(
            settings.blob_quarantine_container
        ).get_blob_client(quarantine_blob_path(skill_id, after2["version"]))
        assert await q_blob.exists()
    finally:
        with contextlib.suppress(Exception):
            await blob.close()

    # 6. Audit row recorded.
    quar_rows = [
        r
        async for r in audit_c.query_items(
            query="SELECT * FROM c WHERE c.skill_id=@id AND c.action='quarantine'",
            parameters=[{"name": "@id", "value": skill_id}],
            partition_key=skill_id,
        )
    ]
    assert len(quar_rows) == 1
    assert quar_rows[0]["metadata"]["justification"] == just

    # 7. Notifier event emitted — `skill.quarantined` payload on the queue.
    qlen = await redis.llen(key_queue_notifications())
    assert qlen >= 1, "expected skill.quarantined event on queue:notifications"
    raw = await redis.blpop([key_queue_notifications()], timeout=2)
    assert raw is not None
    import json

    payload = json.loads(raw[1])
    assert payload["event_type"] == "skill.quarantined"
    assert payload["skill_id"] == skill_id
    assert payload["payload"]["justification"] == just

    await _cleanup(settings, "m5-e2e-flagged-skill")
