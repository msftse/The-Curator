from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.core.config import Settings
from backend.core.redis import key_queue_classifier
from backend.services.janitor import janitor_classifier_queue


class _FakeSkillsContainer:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.last_query: str | None = None

    def query_items(self, *, query: str, parameters: list[dict]):
        self.last_query = query
        cutoff = datetime.fromisoformat(parameters[0]["value"])

        async def _gen():
            for row in self._rows:
                if row.get("classifier_status") not in ("queued", "running", "failed"):
                    continue
                if row.get("status") not in ("pending", "classified", "approved"):
                    continue
                uploaded_at = row.get("uploaded_at")
                if uploaded_at and datetime.fromisoformat(uploaded_at) < cutoff:
                    yield row

        return _gen()


class _FakeAuditContainer:
    def __init__(self) -> None:
        self.items: list[dict] = []

    async def create_item(self, *, body: dict) -> None:
        self.items.append(body)


class _FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


def _settings() -> Settings:
    return Settings(  # type: ignore[arg-type]
        classifier_blpop_timeout_seconds=5,
        janitor_classifier_stale_multiplier=60,
    )


def _doc(
    *,
    doc_id: str,
    skill_id: str,
    classifier_status: str,
    status: str,
    uploaded_offset_s: int,
    now: datetime,
) -> dict:
    return {
        "id": doc_id,
        "skill_id": skill_id,
        "classifier_status": classifier_status,
        "status": status,
        "uploaded_at": (now - timedelta(seconds=uploaded_offset_s)).isoformat(),
    }


@pytest.mark.asyncio
async def test_classifier_janitor_requeues_stale_queued_running_and_failed():
    now = datetime.now(UTC)
    rows = [
        _doc(
            doc_id="d1",
            skill_id="s1",
            classifier_status="queued",
            status="pending",
            uploaded_offset_s=1000,
            now=now,
        ),
        _doc(
            doc_id="d2",
            skill_id="s2",
            classifier_status="running",
            status="pending",
            uploaded_offset_s=1000,
            now=now,
        ),
        _doc(
            doc_id="d3",
            skill_id="s3",
            classifier_status="failed",
            status="classified",
            uploaded_offset_s=1000,
            now=now,
        ),
        _doc(
            doc_id="approved",
            skill_id="approved",
            classifier_status="failed",
            status="approved",
            uploaded_offset_s=1000,
            now=now,
        ),
        _doc(
            doc_id="fresh",
            skill_id="fresh",
            classifier_status="failed",
            status="pending",
            uploaded_offset_s=10,
            now=now,
        ),
        _doc(
            doc_id="terminal",
            skill_id="terminal",
            classifier_status="failed",
            status="quarantined",
            uploaded_offset_s=1000,
            now=now,
        ),
    ]
    skills = _FakeSkillsContainer(rows)
    audit = _FakeAuditContainer()
    redis = _FakeRedis()

    result = await janitor_classifier_queue(
        skills=skills,
        audit=audit,
        redis=redis,
        settings=_settings(),
        now=now,
    )

    assert result == {"scanned": 4, "requeued": 4}
    assert redis.lists[key_queue_classifier()] == ["d1", "d2", "d3", "approved"]
    assert "classifier_status IN ('queued', 'running', 'failed')" in (skills.last_query or "")
    assert len(audit.items) == 4
    assert {row["metadata"]["classifier_status"] for row in audit.items} == {
        "queued",
        "running",
        "failed",
    }
