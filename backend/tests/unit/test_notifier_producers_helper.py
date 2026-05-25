"""`enqueue_notification` + `make_idempotency_key` (M5-6)."""

from __future__ import annotations

import pytest

from backend.core.redis import key_queue_notifications
from backend.models.notifications import NotificationEvent
from backend.services.notifier import (
    build_event,
    enqueue_notification,
    make_idempotency_key,
)


class _FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.pushed: list[tuple[str, str]] = []
        self._fail = fail

    async def rpush(self, key: str, value: str) -> int:
        if self._fail:
            raise RuntimeError("redis down")
        self.pushed.append((key, value))
        return 1


# ---- make_idempotency_key ---------------------------------------------


def test_idempotency_key_is_deterministic():
    a = make_idempotency_key("skill.uploaded", skill_id="s", version="1.0.0", extra="abc")
    b = make_idempotency_key("skill.uploaded", skill_id="s", version="1.0.0", extra="abc")
    assert a == b


def test_idempotency_key_changes_with_inputs():
    base = make_idempotency_key("skill.uploaded", skill_id="s", version="1.0.0")
    assert make_idempotency_key("skill.uploaded", skill_id="s2", version="1.0.0") != base
    assert make_idempotency_key("skill.uploaded", skill_id="s", version="2.0.0") != base
    assert make_idempotency_key("skill.approved", skill_id="s", version="1.0.0") != base


# ---- enqueue_notification ---------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_pushes_serialized_event_to_correct_key():
    redis = _FakeRedis()
    ev = build_event(
        "skill.uploaded",
        skill_id="s",
        payload={"x": 1},
        idempotency_key=make_idempotency_key("skill.uploaded", skill_id="s"),
    )
    ok = await enqueue_notification(ev, redis=redis)
    assert ok is True
    assert len(redis.pushed) == 1
    key, payload = redis.pushed[0]
    assert key == key_queue_notifications()
    round_trip = NotificationEvent.model_validate_json(payload)
    assert round_trip.event_type == "skill.uploaded"
    assert round_trip.payload == {"x": 1}
    assert round_trip.idempotency_key == ev.idempotency_key


@pytest.mark.asyncio
async def test_enqueue_swallows_redis_failure():
    redis = _FakeRedis(fail=True)
    ev = build_event("skill.uploaded", skill_id="s")
    ok = await enqueue_notification(ev, redis=redis)
    assert ok is False  # call did NOT raise


@pytest.mark.asyncio
async def test_enqueue_fills_idempotency_key_if_blank():
    redis = _FakeRedis()
    ev = build_event("skill.uploaded", skill_id="s")
    assert ev.idempotency_key == ""
    await enqueue_notification(ev, redis=redis)
    # event was mutated in place by ensure_idempotency_key
    assert ev.idempotency_key
    sent = NotificationEvent.model_validate_json(redis.pushed[0][1])
    assert sent.idempotency_key == ev.idempotency_key
