"""Integration: curator rollback round-trip (byte-for-byte + reversible).

Asserts that after a curator pass archives published bundles, a subsequent
rollback restores `published/{id}/{ver}/bundle.tar.gz` byte-for-byte AND
produces a `pre-rollback-{ts}` snapshot so the rollback itself is reversible.

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
    published_blob_path,
    put_published,
)
from backend.core.config import get_settings
from backend.core.cosmos import get_cosmos_client
from backend.services import snapshot as snapshot_svc
from backend.services.skill_bundle import slugify

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _seed_skill_with_blob(
    settings,
    blob_service,
    *,
    skill_id: str,
    version: str = "1.0.0",
    bundle_bytes: bytes,
) -> None:
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
        skills = db.get_container_client("skills")
        # last_loaded_at well past archive threshold so planner emits `archive`.
        old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
        sha = hashlib.sha256(bundle_bytes).hexdigest()
        await skills.upsert_item(
            body={
                "id": f"{skill_id}::{version}",
                "skill_id": skill_id,
                "version": version,
                "name": skill_id,
                "description": "rollback round-trip test",
                "uploader": "alice@example.com",
                "status": "approved",
                "classifier_status": "done",
                "pinned": False,
                "uploaded_at": old,
                "approved_at": old,
                "bundle": {
                    "blob_url": f"http://test/{skill_id}",
                    "checksum_sha256": sha,
                    "size_bytes": len(bundle_bytes),
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
        version=version,
        data=bundle_bytes,
    )


async def _blob_sha256(blob_service, settings, *, container: str, name: str) -> str | None:
    cont = blob_service.get_container_client(container)
    client = cont.get_blob_client(name)
    try:
        downloader = await client.download_blob()
        data = await downloader.readall()
    except Exception:
        return None
    return hashlib.sha256(data).hexdigest()


async def _cleanup(
    settings,
    blob_service,
    *,
    skill_ids: list[str],
    snapshot_names: list[str],
) -> None:
    # Cosmos cleanup — `delete_item` is allowed in tests (precedent:
    # backend/tests/integration/test_usage_pipeline.py:37-56). Production
    # code paths are the ones forbidden from deleting skill bytes.
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

    # Blob cleanup — scoped strictly to test-created blobs.
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
        # Also clean any matching curator run-report folder.
        with contextlib.suppress(Exception):
            async for b in reports.list_blobs(
                name_starts_with=f"{settings.curator_runs_container_prefix}/{folder}/"
            ):
                with contextlib.suppress(Exception):
                    await reports.get_blob_client(b.name).delete_blob()


async def test_rollback_restores_blobs_byte_for_byte_and_is_reversible(app_client, as_admin):
    client, app = app_client
    settings = get_settings()
    blob_service = get_blob_service(settings)

    skill_ids = [slugify(f"rollback-rt-{i}") for i in range(3)]
    pre_sha: dict[str, str] = {}
    run_snapshot: str | None = None
    pre_rollback: str | None = None

    try:
        # --- Seed: 3 approved skills with real bundle bytes ---
        for sid in skill_ids:
            bundle = b"bundle-" + sid.encode()
            await _seed_skill_with_blob(settings, blob_service, skill_id=sid, bundle_bytes=bundle)
            sha = await _blob_sha256(
                blob_service,
                settings,
                container=settings.blob_published_container,
                name=published_blob_path(sid, "1.0.0"),
            )
            assert sha is not None
            pre_sha[sid] = sha

        # --- Curator real run: should archive all three ---
        as_admin(app, email="admin@example.com")
        r = await client.post("/v1/admin/curator/run")
        assert r.status_code == 200, r.text
        body = r.json()
        run_snapshot = body["snapshot_name"]
        assert run_snapshot, "real curator run must produce a snapshot"

        # Verify each skill is archived in Cosmos AND archive/ has bytes.
        cosmos = get_cosmos_client(settings)
        try:
            db = cosmos.get_database_client(settings.cosmos_db_name)
            skills = db.get_container_client("skills")
            for sid in skill_ids:
                doc = await skills.read_item(item=f"{sid}::1.0.0", partition_key=sid)
                assert doc["status"] == "archived", sid
                arch_sha = await _blob_sha256(
                    blob_service,
                    settings,
                    container=settings.blob_archive_container,
                    name=published_blob_path(sid, "1.0.0"),
                )
                assert arch_sha == pre_sha[sid], sid
        finally:
            await cosmos.close()

        # --- Corrupt `published/` so the rollback assertion is meaningful ---
        # The curator intentionally leaves source bytes in published/ as
        # defense-in-depth. Overwriting them here is what makes the round-trip
        # assertion prove the rollback restored bytes from the snapshot rather
        # than passively avoiding mutation.
        published = blob_service.get_container_client(settings.blob_published_container)
        for sid in skill_ids:
            corrupt = b"corrupted-" + sid.encode()
            await published.get_blob_client(published_blob_path(sid, "1.0.0")).upload_blob(
                corrupt, overwrite=True
            )

        # --- Rollback ---
        r = await client.post(f"/v1/admin/curator/rollback?id={run_snapshot}")
        assert r.status_code == 200, r.text
        rollback_body = r.json()
        pre_rollback = rollback_body["pre_rollback_snapshot_name"]
        assert pre_rollback and pre_rollback.startswith("pre-rollback-")

        # --- Assert byte-for-byte equality after rollback ---
        for sid in skill_ids:
            post_sha = await _blob_sha256(
                blob_service,
                settings,
                container=settings.blob_published_container,
                name=published_blob_path(sid, "1.0.0"),
            )
            assert post_sha == pre_sha[sid], (
                f"{sid}: rollback failed to restore bytes byte-for-byte"
            )

        # --- Assert Cosmos status restored to approved ---
        cosmos = get_cosmos_client(settings)
        try:
            db = cosmos.get_database_client(settings.cosmos_db_name)
            skills = db.get_container_client("skills")
            audit = db.get_container_client("audit")
            for sid in skill_ids:
                doc = await skills.read_item(item=f"{sid}::1.0.0", partition_key=sid)
                assert doc["status"] == "approved", sid

                # Audit: at least one rollback row + at least one archive row.
                actions: list[str] = []
                async for row in audit.query_items(
                    query="SELECT c.action FROM c WHERE c.skill_id=@id",
                    parameters=[{"name": "@id", "value": sid}],
                    partition_key=sid,
                ):
                    actions.append(row["action"])
                assert "rollback" in actions, sid
                assert "archive" in actions, sid
        finally:
            await cosmos.close()

        # --- Assert pre-rollback snapshot exists (rollback is reversible) ---
        names = await snapshot_svc.list_snapshots(blob_service, settings)
        assert pre_rollback in names

    finally:
        await _cleanup(
            settings,
            blob_service,
            skill_ids=skill_ids,
            snapshot_names=[n for n in (run_snapshot, pre_rollback) if n],
        )
        with contextlib.suppress(Exception):
            await blob_service.close()
