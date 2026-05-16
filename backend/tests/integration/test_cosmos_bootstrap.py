"""Smoke test that the three Cosmos containers exist with expected partition keys + TTL."""

from __future__ import annotations

import pytest

from backend.core.config import get_settings
from backend.core.cosmos import (
    AUDIT_CONTAINER,
    SKILLS_CONTAINER,
    USAGE_EVENTS_CONTAINER,
    USAGE_EVENTS_TTL_SECONDS,
    ensure_containers,
    get_cosmos_client,
)

pytestmark = pytest.mark.integration


async def test_ensure_containers_idempotent():
    settings = get_settings()
    client = get_cosmos_client(settings)
    try:
        db = await ensure_containers(client, settings.cosmos_db_name)
        # Second call must be a no-op.
        db = await ensure_containers(client, settings.cosmos_db_name)
        containers = [c async for c in db.list_containers()]
        names = {c["id"] for c in containers}
        assert {SKILLS_CONTAINER, AUDIT_CONTAINER, USAGE_EVENTS_CONTAINER}.issubset(names)
        # Check TTL on usage_events.
        usage_props = await db.get_container_client(USAGE_EVENTS_CONTAINER).read()
        assert usage_props.get("defaultTtl") == USAGE_EVENTS_TTL_SECONDS
    finally:
        await client.close()
