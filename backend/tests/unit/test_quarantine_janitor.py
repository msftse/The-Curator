"""Quarantine janitor unit tests (M5-3).

Drives `backend.services.quarantine_janitor.move_to_deleted_after_retention`
against in-memory fakes for Cosmos + Blob. Asserts:

- Blobs whose Cosmos doc has `quarantine_expires_at <= now` are deleted.
- Blobs whose `quarantine_expires_at > now` are left alone.
- Blobs without a matching Cosmos doc are skipped (fail-safe orphan).
- Blobs whose doc lacks `quarantine_expires_at` are skipped + logged.
- Every delete writes an audit row with `action='quarantine_delete'`.
- The Cosmos doc itself is NEVER deleted — `skills.items` is unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.core.config import Settings
from backend.models.skill import SkillDoc
from backend.services.quarantine_janitor import (
    move_to_deleted_after_retention,
    run_sweep,
)


# ---- in-memory fakes -------------------------------------------------


class _FakeSkillsContainer:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def query_items(self, *, query, parameters, partition_key=None):  # noqa: ARG002
        skill_id = next((p["value"] for p in parameters if p["name"] == "@id"), None)
        for body in self.items.values():
            if body.get("skill_id") == skill_id:
                yield body


class _FakeAuditContainer:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def create_item(self, *, body: dict[str, Any]) -> None:
        self.rows.append(body)


class _Blob:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeBlobClient:
    def __init__(self, container, name: str) -> None:
        self._container = container
        self._name = name

    async def delete_blob(self) -> None:
        self._container.blobs.pop(self._name, None)
        self._container.deleted.append(self._name)


class _FakeContainerClient:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def list_blobs(self):
        for name in list(self.blobs.keys()):
            yield _Blob(name)

    def get_blob_client(self, name: str) -> _FakeBlobClient:
        return _FakeBlobClient(self, name)


class _FakeBlobService:
    def __init__(self) -> None:
        self.containers: dict[str, _FakeContainerClient] = {}

    def get_container_client(self, name: str) -> _FakeContainerClient:
        return self.containers.setdefault(name, _FakeContainerClient())


# ---- helpers ---------------------------------------------------------


def _settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _seed_doc(
    skills: _FakeSkillsContainer,
    *,
    skill_id: str,
    expires_at: datetime | None,
) -> SkillDoc:
    doc = SkillDoc(
        id=f"{skill_id}:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id=skill_id,
        version="1.0.0",
        name=skill_id,
        description="",
        status="quarantined",
        classifier_status="done",
        uploader="alice@org",
        defender_status="flagged",
        defender_severity="high",
        quarantined_at=datetime.now(UTC),
        quarantined_by="admin@org",
        quarantine_justification="bad bundle exfiltrates env vars",
        quarantine_expires_at=expires_at,
    )
    skills.items[doc.id] = doc.model_dump(mode="json")
    return doc


def _put_blob(blob: _FakeBlobService, container_name: str, name: str) -> None:
    c = blob.get_container_client(container_name)
    c.blobs[name] = b"tar-bytes"


# ---- tests -----------------------------------------------------------


async def test_deletes_expired_and_keeps_active():
    settings = _settings()
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    blob = _FakeBlobService()
    now = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)

    expired = _seed_doc(
        skills, skill_id="expired-skill", expires_at=now - timedelta(days=1)
    )
    active = _seed_doc(
        skills, skill_id="active-skill", expires_at=now + timedelta(days=15)
    )
    _put_blob(blob, settings.blob_quarantine_container, f"{expired.skill_id}/1.0.0/bundle.tar.gz")
    _put_blob(blob, settings.blob_quarantine_container, f"{active.skill_id}/1.0.0/bundle.tar.gz")

    pre_skills_count = len(skills.items)

    result = await move_to_deleted_after_retention(
        blob=blob,
        skills=skills,
        audit=audit,
        settings=settings,
        now=now,
    )

    assert result == {
        "scanned": 2,
        "deleted": 1,
        "skipped_orphan": 0,
        "skipped_active": 1,
    }

    qc = blob.containers[settings.blob_quarantine_container]
    assert f"{expired.skill_id}/1.0.0/bundle.tar.gz" not in qc.blobs
    assert f"{active.skill_id}/1.0.0/bundle.tar.gz" in qc.blobs

    # Cosmos docs are NEVER deleted (AGENTS.md §5).
    assert len(skills.items) == pre_skills_count

    # Audit row written for the delete.
    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["action"] == "quarantine_delete"
    assert row["actor"] == "system:quarantine_janitor"
    assert row["skill_id"] == expired.skill_id


async def test_orphan_blob_is_skipped_and_logged():
    settings = _settings()
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    blob = _FakeBlobService()
    now = datetime(2026, 5, 21, tzinfo=UTC)

    _put_blob(blob, settings.blob_quarantine_container, "ghost-skill/1.0.0/bundle.tar.gz")

    result = await move_to_deleted_after_retention(
        blob=blob,
        skills=skills,
        audit=audit,
        settings=settings,
        now=now,
    )

    assert result["scanned"] == 1
    assert result["deleted"] == 0
    assert result["skipped_orphan"] == 1
    qc = blob.containers[settings.blob_quarantine_container]
    assert "ghost-skill/1.0.0/bundle.tar.gz" in qc.blobs  # not removed
    assert audit.rows == []


async def test_doc_without_expires_at_is_skipped():
    settings = _settings()
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    blob = _FakeBlobService()
    now = datetime(2026, 5, 21, tzinfo=UTC)

    weird = _seed_doc(skills, skill_id="weird-skill", expires_at=None)
    _put_blob(blob, settings.blob_quarantine_container, f"{weird.skill_id}/1.0.0/bundle.tar.gz")

    result = await move_to_deleted_after_retention(
        blob=blob,
        skills=skills,
        audit=audit,
        settings=settings,
        now=now,
    )
    assert result["deleted"] == 0
    assert result["skipped_orphan"] == 1


async def test_run_sweep_is_a_thin_wrapper():
    settings = _settings()
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    blob = _FakeBlobService()
    now = datetime(2026, 5, 21, tzinfo=UTC)
    out = await run_sweep(
        blob=blob, skills=skills, audit=audit, settings=settings, now=now
    )
    assert out == {
        "scanned": 0,
        "deleted": 0,
        "skipped_orphan": 0,
        "skipped_active": 0,
    }


async def test_unexpected_blob_name_is_skipped():
    settings = _settings()
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    blob = _FakeBlobService()
    now = datetime(2026, 5, 21, tzinfo=UTC)

    # Wrong layout — janitor should ignore (not crash, not delete).
    _put_blob(blob, settings.blob_quarantine_container, "stray.txt")
    _put_blob(blob, settings.blob_quarantine_container, "shallow/file.tar.gz")

    result = await move_to_deleted_after_retention(
        blob=blob,
        skills=skills,
        audit=audit,
        settings=settings,
        now=now,
    )
    assert result["deleted"] == 0
    qc = blob.containers[settings.blob_quarantine_container]
    assert set(qc.blobs.keys()) == {"stray.txt", "shallow/file.tar.gz"}
