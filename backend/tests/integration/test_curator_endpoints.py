"""Integration: curator pause/resume/status endpoints + pin/unpin.

Requires emulator stack.
"""

from __future__ import annotations

import httpx
import pytest

from backend.app import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, app


async def test_pause_resume_status(app_client, as_admin):
    client, app = app_client
    as_admin(app, email="admin@example.com")

    r = await client.post("/v1/admin/curator/pause")
    assert r.status_code == 200, r.text
    assert r.json()["paused"] is True

    s = await client.get("/v1/admin/curator/status")
    assert s.status_code == 200
    assert s.json()["paused"] is True

    # Running while paused is a 409.
    run = await client.post("/v1/admin/curator/run")
    assert run.status_code == 409

    r2 = await client.post("/v1/admin/curator/resume")
    assert r2.status_code == 200
    assert r2.json()["paused"] is False

    s2 = await client.get("/v1/admin/curator/status")
    assert s2.json()["paused"] is False


async def test_admin_endpoints_require_admin(app_client, as_user):
    client, app = app_client
    as_user(app, email="alice@example.com")

    for path in (
        "/v1/admin/curator/pause",
        "/v1/admin/curator/resume",
        "/v1/admin/curator/run",
        "/v1/admin/curator/janitor",
    ):
        r = await client.post(path)
        assert r.status_code == 403, f"{path} should require admin"
