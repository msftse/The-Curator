"""Integration: pinning grants immunity across multiple curator passes.

Asserts that a pinned skill survives two curator passes (one at stale+1,
one at archive+1) and only transitions after `POST /unpin/{id}`. Also
verifies the control (unpinned) skill transitions normally, proving the
planner was actually engaged.

Requires emulator stack. Skipped automatically otherwise.
"""

from __future__ import annotations

import contextlib
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.app import create_app
from backend.core.blob import (
    get_blob_service,
    put_published,
)
from backend.core.config import get_settings
from backend.core.cosmos import get_cosmos_client
from backend.services.curator import execute_pass
from backend.services.skill_bundle import slugify

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _seed_approved_skill_with_blob(
    settings,
    blob_service,
    *,
    skill_id: str,
    pinned: bool,
    baseline_dt: datetime,
) -> None:
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
        skills = db.get_container_client("skills")
        old = (baseline_dt - timedelta(days=200)).isoformat()
        bundle = b"bundle-" + skill_id.encode()
        sha = hashlib.sha256(bundle).hexdigest()
        await skills.upsert_item(
            body={
                "id": f"{skill_id}::1.0.0",
                "skill_id": skill_id,
                "version": "1.0.0",
                "name": skill_id,
                "description": "pin/unpin test",
                "uploader": "alice@example.com",
                "status": "approved",
                "classifier_status": "done",
                "pinned": pinned,
                "pinned_by": "admin@example.com" if pinned else None,
                "uploaded_at": old,
                "approved_at": old,
                "bundle": {
                    "blob_url": f"http://test/{skill_id}",
                    "checksum_sha256": sha,
                    "size_bytes": len(bundle),
                    "file_count": 1,
                },
                "usage": {
                    "load_count": 1,
                    "last_loaded_at": old,
                    "loaders_30d": 0,
                },
            }
        )
    finally:
        await cosmos.close()

    await put_published(
        blob_service,
        settings,
        skill_id=skill_id,
        version="1.0.0",
        data=bundle,
    )


async def _cleanup(
    settings,
    blob_service,
    *,
    skill_ids: list[str],
    snapshot_names: list[str],
    run_ids: list[str],
) -> None:
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

    published = blob_service.get_container_client(settings.blob_published_container)
    archive = blob_service.get_container_client(settings.blob_archive_container)
    snapshots = blob_service.get_container_client(settings.blob_snapshots_container)
    reports = blob_service.get_container_client(settings.curator_reports_container)

    for sid in skill_ids:
        for cont in (published, archive):
            with contextlib.suppress(Exception):
                async for b in cont.list_blobs(name_starts_with=f"{sid}/"):
                    with contextlib.suppress(Exception):
                        await cont.get_blob_client(b.name).delete_blob()

    for folder in snapshot_names:
        if not folder:
            continue
        with contextlib.suppress(Exception):
            async for b in snapshots.list_blobs(name_starts_with=f"{folder}/"):
                with contextlib.suppress(Exception):
                    await snapshots.get_blob_client(b.name).delete_blob()

    for run_id in run_ids:
        if not run_id:
            continue
        with contextlib.suppress(Exception):
            async for b in reports.list_blobs(
                name_starts_with=f"{settings.curator_runs_container_prefix}/{run_id}/"
            ):
                with contextlib.suppress(Exception):
                    await reports.get_blob_client(b.name).delete_blob()


async def test_pinned_skill_survives_curator_passes_then_transitions_when_unpinned(
    app_client, as_admin
):
    client, app = app_client
    settings = get_settings()
    blob_service = get_blob_service(settings)

    stale = settings.curator_stale_days
    archive_days = settings.curator_archive_days
    baseline = datetime(2026, 1, 1, tzinfo=UTC)

    pinned_id = slugify("pin-test-keeps")
    control_id = slugify("pin-test-control")
    skill_ids = [pinned_id, control_id]

    run_ids: list[str] = []
    snapshot_names: list[str] = []

    db = app.state.cosmos_db
    skills_container = db.get_container_client("skills")
    audit_container = db.get_container_client("audit")
    system_state = db.get_container_client("system_state")
    redis = app.state.redis
    blob_app = app.state.blob

    try:
        await _seed_approved_skill_with_blob(
            settings,
            blob_service,
            skill_id=pinned_id,
            pinned=True,
            baseline_dt=baseline,
        )
        await _seed_approved_skill_with_blob(
            settings,
            blob_service,
            skill_id=control_id,
            pinned=False,
            baseline_dt=baseline,
        )

        # --- Pass 1: stale-eligible age ---
        now1 = baseline + timedelta(days=stale + 1)
        record1 = await execute_pass(
            dry_run=False,
            skills=skills_container,
            audit=audit_container,
            blob=blob_app,
            redis=redis,
            system_state=system_state,
            settings=settings,
            now=now1,
        )
        run_ids.append(record1.run_id)
        if record1.snapshot_name:
            snapshot_names.append(record1.snapshot_name)
        assert pinned_id in record1.skipped_pinned

        # --- Pass 2: archive-eligible age ---
        now2 = baseline + timedelta(days=archive_days + 1)
        record2 = await execute_pass(
            dry_run=False,
            skills=skills_container,
            audit=audit_container,
            blob=blob_app,
            redis=redis,
            system_state=system_state,
            settings=settings,
            now=now2,
        )
        run_ids.append(record2.run_id)
        if record2.snapshot_name:
            snapshot_names.append(record2.snapshot_name)
        assert pinned_id in record2.skipped_pinned

        # --- State after A+B ---
        pinned_doc = await skills_container.read_item(
            item=f"{pinned_id}::1.0.0", partition_key=pinned_id
        )
        assert pinned_doc["status"] == "approved"
        assert pinned_doc["pinned"] is True

        control_doc = await skills_container.read_item(
            item=f"{control_id}::1.0.0", partition_key=control_id
        )
        assert control_doc["status"] == "archived"

        pinned_actions: list[str] = []
        async for r in audit_container.query_items(
            query="SELECT c.action FROM c WHERE c.skill_id=@id",
            parameters=[{"name": "@id", "value": pinned_id}],
            partition_key=pinned_id,
        ):
            pinned_actions.append(r["action"])
        assert "archive" not in pinned_actions
        assert "stale" not in pinned_actions

        control_actions: list[str] = []
        async for r in audit_container.query_items(
            query="SELECT c.action FROM c WHERE c.skill_id=@id",
            parameters=[{"name": "@id", "value": control_id}],
            partition_key=control_id,
        ):
            control_actions.append(r["action"])
        assert "archive" in control_actions

        # --- Unpin via HTTP ---
        as_admin(app, email="admin@example.com")
        r = await client.post(f"/v1/admin/curator/unpin/{pinned_id}")
        assert r.status_code == 200, r.text
        assert r.json()["pinned"] is False

        pinned_doc2 = await skills_container.read_item(
            item=f"{pinned_id}::1.0.0", partition_key=pinned_id
        )
        assert pinned_doc2["pinned"] is False

        # --- Pass 3: archive-eligible age, no longer pinned ---
        now3 = now2
        record3 = await execute_pass(
            dry_run=False,
            skills=skills_container,
            audit=audit_container,
            blob=blob_app,
            redis=redis,
            system_state=system_state,
            settings=settings,
            now=now3,
        )
        run_ids.append(record3.run_id)
        if record3.snapshot_name:
            snapshot_names.append(record3.snapshot_name)
        assert pinned_id not in record3.skipped_pinned

        # --- Final state ---
        pinned_doc3 = await skills_container.read_item(
            item=f"{pinned_id}::1.0.0", partition_key=pinned_id
        )
        assert pinned_doc3["status"] == "archived"

        actions_final: list[str] = []
        async for r in audit_container.query_items(
            query="SELECT c.action FROM c WHERE c.skill_id=@id",
            parameters=[{"name": "@id", "value": pinned_id}],
            partition_key=pinned_id,
        ):
            actions_final.append(r["action"])
        assert "archive" in actions_final
        assert "unpin" in actions_final
    finally:
        await _cleanup(
            settings,
            blob_service,
            skill_ids=skill_ids,
            snapshot_names=snapshot_names,
            run_ids=run_ids,
        )
        with contextlib.suppress(Exception):
            await blob_service.close()
