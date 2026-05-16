"""Integration: admin-issued manual archive of approved skills.

`POST /v1/admin/skills/{skill_id}/archive` is the admin "delete" surface.
It must:

  - Refuse non-admin callers (403).
  - Refuse a reason-less body (422 validation from `ArchiveRequest`).
  - Refuse pinned skills (409 `SKILL_PINNED`).
  - Refuse non-approved skills (409 `INVALID_STATUS_TRANSITION`).
  - On the happy path:
      * Flip Cosmos `status` → `archived`.
      * MOVE bundle from `published/` to `archive/` (copy then delete
        source — AGENTS.md §5 archive=move, verified by `dest.exists()`
        before the source delete).
      * Write an audit row with `action='archive'`, the supplied reason,
        and `source='admin_manual'` metadata.
      * Drop the skill out of the public catalog (`status` filter).
      * Be reversible via the existing
        `POST /v1/admin/curator/restore/{skill_id}` endpoint, which
        copies `archive/`→`published/` and flips status back.

Mirrors the seeding pattern in `test_curator_pin_unpin.py`. Requires the
docker-compose emulator stack.
"""

from __future__ import annotations

import contextlib
import hashlib
from datetime import UTC, datetime

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
from backend.services.skill_bundle import slugify

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def _seed_approved(
    settings,
    blob_service,
    *,
    skill_id: str,
    pinned: bool = False,
    status: str = "approved",
) -> None:
    cosmos = get_cosmos_client(settings)
    try:
        db = cosmos.get_database_client(settings.cosmos_db_name)
        skills = db.get_container_client("skills")
        now = datetime.now(UTC).isoformat()
        bundle = b"bundle-" + skill_id.encode()
        sha = hashlib.sha256(bundle).hexdigest()
        await skills.upsert_item(
            body={
                "id": f"{skill_id}::1.0.0",
                "skill_id": skill_id,
                "version": "1.0.0",
                "name": skill_id,
                "description": "admin archive test",
                "uploader": "alice@example.com",
                "status": status,
                "classifier_status": "done",
                "pinned": pinned,
                "pinned_by": "admin@example.com" if pinned else None,
                "uploaded_at": now,
                "approved_at": now if status == "approved" else None,
                "bundle": {
                    "blob_url": f"http://test/{skill_id}",
                    "checksum_sha256": sha,
                    "size_bytes": len(bundle),
                    "file_count": 1,
                },
                "usage": {
                    "load_count": 0,
                    "last_loaded_at": None,
                    "loaders_30d": 0,
                },
            }
        )
    finally:
        await cosmos.close()

    if status == "approved":
        await put_published(
            blob_service,
            settings,
            skill_id=skill_id,
            version="1.0.0",
            data=bundle,
        )


async def _cleanup(settings, blob_service, *, skill_ids: list[str]) -> None:
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
    for sid in skill_ids:
        for cont in (published, archive):
            with contextlib.suppress(Exception):
                async for b in cont.list_blobs(name_starts_with=f"{sid}/"):
                    with contextlib.suppress(Exception):
                        await cont.get_blob_client(b.name).delete_blob()


# --- Happy path: archive → audit → catalog drops → restore round-trip ----


async def test_admin_archive_happy_path_with_restore(app_client, as_admin):
    client, app = app_client
    settings = get_settings()
    blob_service = get_blob_service(settings)

    skill_id = slugify("admin-archive-happy")

    db = app.state.cosmos_db
    skills_container = db.get_container_client("skills")
    audit_container = db.get_container_client("audit")

    try:
        await _seed_approved(settings, blob_service, skill_id=skill_id)
        as_admin(app, email="admin@example.com")

        # 1. Archive.
        r = await client.post(
            f"/v1/admin/skills/{skill_id}/archive",
            json={"reason": "duplicate of foo-bar"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "archived"
        assert r.json()["pinned"] is False

        # 2. Cosmos status flipped.
        doc = await skills_container.read_item(item=f"{skill_id}::1.0.0", partition_key=skill_id)
        assert doc["status"] == "archived"

        # 3. Archive blob exists; published source removed (move, not copy).
        archive_path = published_blob_path(skill_id, "1.0.0")
        archive_blob = blob_service.get_container_client(
            settings.blob_archive_container
        ).get_blob_client(archive_path)
        assert await archive_blob.exists()
        published_blob = blob_service.get_container_client(
            settings.blob_published_container
        ).get_blob_client(archive_path)
        assert not await published_blob.exists()  # AGENTS.md §5: archive = move.

        # 4. Audit row recorded with reason + admin_manual metadata.
        archive_rows: list[dict] = []
        async for row in audit_container.query_items(
            query=("SELECT * FROM c WHERE c.skill_id=@id AND c.action='archive'"),
            parameters=[{"name": "@id", "value": skill_id}],
            partition_key=skill_id,
        ):
            archive_rows.append(row)
        assert len(archive_rows) == 1, archive_rows
        meta = archive_rows[0].get("metadata") or {}
        assert meta.get("reason") == "duplicate of foo-bar"
        assert meta.get("source") == "admin_manual"
        assert archive_rows[0]["actor"] == "admin@example.com"

        # 5. Public catalog filters it out.
        list_resp = await client.get("/v1/skills")
        assert list_resp.status_code == 200
        ids = [s["skill_id"] for s in list_resp.json()]
        assert skill_id not in ids

        # 6. Restore round-trips via existing curator restore endpoint.
        restore = await client.post(f"/v1/admin/curator/restore/{skill_id}")
        assert restore.status_code == 200, restore.text
        assert restore.json()["status"] == "approved"

        doc2 = await skills_container.read_item(item=f"{skill_id}::1.0.0", partition_key=skill_id)
        assert doc2["status"] == "approved"
    finally:
        await _cleanup(settings, blob_service, skill_ids=[skill_id])
        with contextlib.suppress(Exception):
            await blob_service.close()


# --- Refusal: pinned skill ----------------------------------------------


async def test_admin_archive_refuses_pinned(app_client, as_admin):
    client, app = app_client
    settings = get_settings()
    blob_service = get_blob_service(settings)

    skill_id = slugify("admin-archive-pinned")

    db = app.state.cosmos_db
    skills_container = db.get_container_client("skills")

    try:
        await _seed_approved(settings, blob_service, skill_id=skill_id, pinned=True)
        as_admin(app, email="admin@example.com")

        r = await client.post(
            f"/v1/admin/skills/{skill_id}/archive",
            json={"reason": "should be blocked"},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "SKILL_PINNED"

        doc = await skills_container.read_item(item=f"{skill_id}::1.0.0", partition_key=skill_id)
        assert doc["status"] == "approved"
        assert doc["pinned"] is True
    finally:
        await _cleanup(settings, blob_service, skill_ids=[skill_id])
        with contextlib.suppress(Exception):
            await blob_service.close()


# --- Refusal: non-approved status ---------------------------------------


async def test_admin_archive_refuses_non_approved(app_client, as_admin):
    client, app = app_client
    settings = get_settings()
    blob_service = get_blob_service(settings)

    skill_id = slugify("admin-archive-pending")

    try:
        await _seed_approved(settings, blob_service, skill_id=skill_id, status="pending")
        as_admin(app, email="admin@example.com")

        r = await client.post(
            f"/v1/admin/skills/{skill_id}/archive",
            json={"reason": "wrong status"},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "INVALID_STATUS_TRANSITION"
    finally:
        await _cleanup(settings, blob_service, skill_ids=[skill_id])
        with contextlib.suppress(Exception):
            await blob_service.close()


# --- Refusal: non-admin --------------------------------------------------


async def test_admin_archive_requires_admin(app_client, as_user):
    client, app = app_client
    as_user(app, email="alice@example.com")

    r = await client.post(
        "/v1/admin/skills/anything/archive",
        json={"reason": "nope"},
    )
    assert r.status_code == 403


# --- Refusal: missing reason --------------------------------------------


async def test_admin_archive_requires_reason(app_client, as_admin):
    client, app = app_client
    as_admin(app, email="admin@example.com")

    r = await client.post(
        "/v1/admin/skills/anything/archive",
        json={},
    )
    assert r.status_code == 422  # pydantic validation
