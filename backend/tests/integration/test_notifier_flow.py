"""Integration: queue:notifications round-trip with fake ACS (M5-5).

Pushes a NotificationEvent onto the live Redis queue, drives one
`process_one` tick on the notifier worker with fake ACS + fake Graph,
asserts the message was sent and the dedupe key was set.

Skipped automatically when the local emulator stack isn't running. Does
NOT require ACS or Graph credentials — both providers are stubbed.
"""

from __future__ import annotations

import contextlib

import pytest

from backend.core.config import get_settings
from backend.core.cosmos import get_cosmos_client
from backend.core.redis import (
    get_redis,
    key_admin_recipients,
    key_notif_sent,
    key_queue_notifications,
)
from backend.models.notifications import NotificationEvent
from backend.services.notifier import FakeAcsEmailClient, FakeGraphClient
from backend.workers.notifier import process_one as notifier_process_one

pytestmark = pytest.mark.integration


@pytest.fixture
async def stack():
    settings = get_settings()
    cosmos_client = get_cosmos_client(settings)
    redis = get_redis(settings)
    try:
        # Best-effort container creation — the cosmos init is idempotent.
        from backend.core.cosmos import ensure_containers

        with contextlib.suppress(Exception):
            await ensure_containers(cosmos_client, settings.cosmos_db_name)
        yield settings, cosmos_client, redis
    finally:
        with contextlib.suppress(Exception):
            await redis.delete(
                key_queue_notifications(),
                key_admin_recipients(),
                key_notif_sent("intg-notif-1"),
            )
        await redis.aclose()
        await cosmos_client.close()


async def test_notifier_queue_round_trip(stack):
    settings, cosmos_client, redis = stack
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient(recipients=["intg-admin@org"])

    ev = NotificationEvent(
        event_type="skill.uploaded",
        skill_id="intg-skill",
        payload={
            "skill_name": "Integration Skill",
            "skill_id": "intg-skill",
            "version": "1.0.0",
            "uploader": "uploader@org",
            "uploaded_at": "now",
        },
        idempotency_key="intg-notif-1",
    )

    # 1. Producer-side: push the event onto the live Redis queue.
    await redis.rpush(key_queue_notifications(), ev.model_dump_json())
    assert await redis.llen(key_queue_notifications()) == 1

    # 2. Worker-side: pop and process one message.
    msg = await redis.blpop([key_queue_notifications()], timeout=5)
    assert msg is not None
    _key, raw = msg

    await notifier_process_one(
        raw=raw,
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
        acs=acs,
        graph=graph,
    )

    # 3. ACS got the send to the resolved admin recipient.
    assert len(acs.sent) == 1
    sent = acs.sent[0]
    assert sent.recipients == ["intg-admin@org"]
    assert "Integration Skill" in sent.subject

    # 4. Dedupe lock was claimed.
    assert await redis.get(key_notif_sent("intg-notif-1")) == "1"

    # 5. Replaying the same event must NOT re-send.
    await redis.rpush(key_queue_notifications(), raw)
    msg2 = await redis.blpop([key_queue_notifications()], timeout=5)
    assert msg2 is not None
    await notifier_process_one(
        raw=msg2[1],
        cosmos_client=cosmos_client,
        redis=redis,
        settings=settings,
        acs=acs,
        graph=graph,
    )
    # Still one send.
    assert len(acs.sent) == 1
