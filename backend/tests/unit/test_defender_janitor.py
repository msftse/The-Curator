"""Unit tests for the defender janitor (M5 follow-up — plan §3 step 5).

Mirrors the shape of `test_janitor_sweep.py` but with in-process fakes so
this runs in the unit suite without Cosmos / Redis emulators.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from backend.core.config import Settings
from backend.core.redis import key_queue_defender
from backend.services.janitor import janitor_defender_queue


# ---- fakes -----------------------------------------------------------


class _FakeSkillsContainer:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_query: str | None = None
        self.last_params: list[dict[str, Any]] | None = None

    def query_items(self, *, query: str, parameters: list[dict[str, Any]]):
        self.last_query = query
        self.last_params = parameters
        cutoff_iso = parameters[0]["value"]
        cutoff = datetime.fromisoformat(cutoff_iso)

        async def _gen():
            for r in self._rows:
                status = r.get("defender_status")
                if status not in ("pending", "failed"):
                    continue
                scanned_at = r.get("defender_scanned_at")
                uploaded_at = r.get("uploaded_at")
                ref = scanned_at or uploaded_at
                if ref is None:
                    continue
                if datetime.fromisoformat(ref) < cutoff:
                    yield r

        return _gen()


class _FakeAuditContainer:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def create_item(self, *, body: dict[str, Any]) -> None:
        self.items.append(body)


class _FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


def _settings() -> Settings:
    return Settings(  # type: ignore[arg-type]
        defender_blpop_timeout_seconds=5,
        janitor_defender_stale_multiplier=60,  # 5 * 60 = 300s
    )


def _doc(*, doc_id: str, skill_id: str, status: str, scanned_offset_s: int | None, uploaded_offset_s: int, now: datetime) -> dict[str, Any]:
    return {
        "id": doc_id,
        "skill_id": skill_id,
        "defender_status": status,
        "defender_scanned_at": (
            (now - timedelta(seconds=scanned_offset_s)).isoformat()
            if scanned_offset_s is not None
            else None
        ),
        "uploaded_at": (now - timedelta(seconds=uploaded_offset_s)).isoformat(),
    }


# ---- tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_defender_janitor_requeues_stale_pending_and_failed():
    now = datetime.now(UTC)
    rows = [
        # Stale pending (never scanned, uploaded long ago) — requeue.
        _doc(
            doc_id="d1", skill_id="s1", status="pending",
            scanned_offset_s=None, uploaded_offset_s=1000, now=now,
        ),
        # Stale failed (scanned long ago) — requeue.
        _doc(
            doc_id="d2", skill_id="s2", status="failed",
            scanned_offset_s=1000, uploaded_offset_s=2000, now=now,
        ),
        # Fresh pending — leave alone.
        _doc(
            doc_id="d3", skill_id="s3", status="pending",
            scanned_offset_s=None, uploaded_offset_s=10, now=now,
        ),
        # Clean status — never a candidate.
        _doc(
            doc_id="d4", skill_id="s4", status="clean",
            scanned_offset_s=1000, uploaded_offset_s=2000, now=now,
        ),
    ]
    skills = _FakeSkillsContainer(rows)
    audit = _FakeAuditContainer()
    redis = _FakeRedis()
    settings = _settings()

    result = await janitor_defender_queue(
        skills=skills,
        audit=audit,
        redis=redis,
        settings=settings,
        now=now,
    )

    assert result == {"scanned": 2, "requeued": 2}
    # Both stale doc ids landed on queue:defender in order.
    assert redis.lists[key_queue_defender()] == ["d1", "d2"]
    # Audit rows: one per requeue, actor=system:defender_janitor, action=classify.
    assert len(audit.items) == 2
    for row in audit.items:
        assert row["actor"] == "system:defender_janitor"
        assert row["action"] == "classify"
        assert row["metadata"]["requeued_by"] == "defender_janitor"
    skill_ids = {row["skill_id"] for row in audit.items}
    assert skill_ids == {"s1", "s2"}


@pytest.mark.asyncio
async def test_defender_janitor_empty_when_nothing_stale():
    now = datetime.now(UTC)
    rows = [
        _doc(
            doc_id="d1", skill_id="s1", status="pending",
            scanned_offset_s=None, uploaded_offset_s=10, now=now,
        ),
    ]
    skills = _FakeSkillsContainer(rows)
    audit = _FakeAuditContainer()
    redis = _FakeRedis()

    result = await janitor_defender_queue(
        skills=skills,
        audit=audit,
        redis=redis,
        settings=_settings(),
        now=now,
    )
    assert result == {"scanned": 0, "requeued": 0}
    assert redis.lists == {}
    assert audit.items == []
