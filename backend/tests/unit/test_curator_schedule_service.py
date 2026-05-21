"""Unit tests for the curator schedule editor surface (M5-7).

Covers three layers:

  - `backend.models.schedule.validate_cron` — pure-Python parsing rules.
  - `backend.services.curator_schedule` — Cosmos-first upsert + audit
    write, default fallback when no doc exists.
  - Endpoint validation — the Pydantic `CuratorScheduleUpdate` body
    rejects bad cron strings before the handler ever runs (so the auth
    check in the handler isn't even reached).

Endpoint *authorization* (admin-only) is verified by the
`require_role("admin")` dependency, which is exercised end-to-end in
integration tests; here we instead assert that an invalid request body
raises `ValidationError` so the route returns 422 deterministically.
"""

from __future__ import annotations

from typing import Any

import pytest
from azure.cosmos import exceptions as cosmos_exc
from pydantic import ValidationError

from backend.models.schedule import (
    DEFAULT_CRON,
    CuratorSchedule,
    CuratorScheduleUpdate,
    validate_cron,
)
from backend.services import curator_schedule as schedule_svc


# ---- in-memory fakes -------------------------------------------------


class _FakeSystemState:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def read_item(self, *, item: str, partition_key: str) -> dict[str, Any]:  # noqa: ARG002
        if item not in self.items:
            raise cosmos_exc.CosmosResourceNotFoundError(
                status_code=404, message="not found"
            )
        return self.items[item]

    async def upsert_item(self, *, body: dict[str, Any]) -> dict[str, Any]:
        self.items[body["id"]] = dict(body)
        return self.items[body["id"]]


class _FakeAudit:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def create_item(self, *, body: dict[str, Any]) -> None:
        self.rows.append(body)


# ---- validate_cron --------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "0 3 * * 0",
        "*/5 * * * *",
        "0 0,12 * * *",
        "0 3 1-15 * 1-5",
        "30 3 * * *",
    ],
)
def test_validate_cron_accepts_valid(expr: str) -> None:
    assert validate_cron(expr) == expr


@pytest.mark.parametrize(
    "expr",
    [
        "",
        "   ",
        "@hourly",
        "@daily",
        "0 3 * *",  # only 4 fields
        "0 3 * * * *",  # 6 fields
        "60 3 * * *",  # minute out of range
        "0 24 * * *",  # hour out of range
        "0 3 32 * *",  # dom out of range
        "0 3 * 13 *",  # month out of range
        "0 3 * * 7",  # dow out of range
        "MON 3 * * *",  # named token
        "0 3 5-2 * *",  # inverted range
    ],
)
def test_validate_cron_rejects_invalid(expr: str) -> None:
    with pytest.raises(ValueError):
        validate_cron(expr)


def test_curator_schedule_update_body_validates() -> None:
    """The PUT body model is the route's first line of defense."""
    body = CuratorScheduleUpdate(cron="0 3 * * 0", timezone="UTC", enabled=True)
    assert body.cron == "0 3 * * 0"

    with pytest.raises(ValidationError):
        CuratorScheduleUpdate(cron="not-a-cron", timezone="UTC", enabled=True)


# ---- service layer --------------------------------------------------


async def test_get_schedule_returns_default_when_missing() -> None:
    state = _FakeSystemState()
    sched = await schedule_svc.get_schedule(system_state=state)
    assert sched.cron == DEFAULT_CRON
    assert sched.enabled is True
    assert sched.timezone == "UTC"


async def test_put_schedule_writes_cosmos_then_audit() -> None:
    state = _FakeSystemState()
    audit = _FakeAudit()

    result = await schedule_svc.put_schedule(
        system_state=state,
        audit=audit,
        actor="admin@org",
        actor_oid="oid-1",
        cron="*/15 * * * *",
        timezone="UTC",
        enabled=True,
    )

    # Cosmos was written first (single key, exactly one upsert).
    assert "curator_schedule" in state.items
    stored = state.items["curator_schedule"]
    assert stored["cron"] == "*/15 * * * *"
    assert stored["key"] == "curator_schedule"
    assert stored["enabled"] is True
    assert stored["updated_by"] == "admin@org"
    # Returned model mirrors the stored doc.
    assert result.cron == "*/15 * * * *"
    # Audit row carries before/after for diffing.
    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["action"] == "curator_schedule_update"
    assert row["actor"] == "admin@org"
    assert row["actor_oid"] == "oid-1"
    assert row["before"] == {
        "cron": DEFAULT_CRON,
        "timezone": "UTC",
        "enabled": True,
    }
    assert row["after"] == {
        "cron": "*/15 * * * *",
        "timezone": "UTC",
        "enabled": True,
    }


async def test_put_schedule_reflects_disable() -> None:
    state = _FakeSystemState()
    audit = _FakeAudit()
    await schedule_svc.put_schedule(
        system_state=state,
        audit=audit,
        actor="admin@org",
        actor_oid=None,
        cron="0 3 * * 0",
        timezone="UTC",
        enabled=False,
    )
    sched = await schedule_svc.get_schedule(system_state=state)
    assert sched.enabled is False
    assert audit.rows[0]["after"]["enabled"] is False


async def test_get_schedule_round_trips_existing_doc() -> None:
    state = _FakeSystemState()
    state.items["curator_schedule"] = {
        "id": "curator_schedule",
        "key": "curator_schedule",
        "cron": "0 6 * * 1",
        "timezone": "Asia/Jerusalem",
        "enabled": True,
        "updated_by": "alice@org",
        "updated_at": "2026-05-21T11:00:00+00:00",
        "_etag": '"etag-1"',
        "_ts": 1,
    }
    sched = await schedule_svc.get_schedule(system_state=state)
    assert isinstance(sched, CuratorSchedule)
    assert sched.cron == "0 6 * * 1"
    assert sched.timezone == "Asia/Jerusalem"
    assert sched.updated_by == "alice@org"
