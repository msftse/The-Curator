"""Quarantine service unit tests (M5-3).

Drives `backend.services.quarantine.quarantine_skill` against in-memory
fakes for Cosmos / Blob / Redis. Asserts:

- Cosmos-first ordering: doc status flipped to `quarantined`, bundle
  bytes uploaded to quarantine container with verified destination.
- Audit row recorded with action `quarantine` + justification.
- `defender_status != 'flagged'` is refused with `DefenderNotFlagged`.
- Pinned skill is refused with `SkillPinned`.
- Short justification is refused with `JustificationRequired`.
- Redis failure during cache invalidation is swallowed (rule §4 #2).
- `quarantine_expires_at = now + retention_days`.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from azure.core import MatchConditions

from backend.core.config import Settings
from backend.core.errors import (
    DefenderNotFlagged,
    JustificationRequired,
    SkillNotFound,
    SkillPinned,
)
from backend.models.skill import SkillDoc
from backend.services.quarantine import quarantine_skill


# ---- in-memory fakes -------------------------------------------------


class _FakeSkillsContainer:
    """Supports query_items + read_item + replace_item with etag semantics."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self._etag_seq = 1

    def _stamp(self, body: dict[str, Any]) -> dict[str, Any]:
        self._etag_seq += 1
        body["_etag"] = f'"etag-{self._etag_seq}"'
        return body

    async def query_items(self, *, query, parameters, partition_key=None):  # noqa: ARG002
        # Naive: match by skill_id parameter only.
        skill_id = next((p["value"] for p in parameters if p["name"] == "@id"), None)
        for body in self.items.values():
            if body.get("skill_id") == skill_id:
                yield body

    async def read_item(self, *, item: str, partition_key: str) -> dict[str, Any]:  # noqa: ARG002
        return self.items[item]

    async def replace_item(
        self,
        *,
        item: str,
        body: dict[str, Any],
        etag: str | None = None,
        match_condition=None,
    ) -> dict[str, Any]:
        if match_condition == MatchConditions.IfNotModified:
            current = self.items.get(item, {})
            if etag is not None and current.get("_etag") != etag:
                from azure.cosmos import exceptions as cosmos_exc

                raise cosmos_exc.CosmosAccessConditionFailedError(
                    status_code=412, message="etag mismatch"
                )
        new_body = dict(body)
        self._stamp(new_body)
        self.items[item] = new_body
        return new_body


class _FakeAuditContainer:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def create_item(self, *, body: dict[str, Any]) -> None:
        self.rows.append(body)


class _FakeBlobClient:
    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path

    async def upload_blob(self, data, overwrite: bool = False) -> None:  # noqa: ARG002
        # Accept both bytes and bytes-like.
        if not isinstance(data, bytes | bytearray):
            data = bytes(data)
        self._store[self._path] = bytes(data)

    async def exists(self) -> bool:
        return self._path in self._store

    async def download_blob(self):
        outer = self

        class _Downloader:
            async def readall(self_inner):  # noqa: ARG002
                return outer._store[outer._path]

        return _Downloader()

    async def delete_blob(self) -> None:
        self._store.pop(self._path, None)


class _FakeContainerClient:
    def __init__(self, name: str, stores: dict[str, dict[str, bytes]]) -> None:
        self._name = name
        self._store = stores.setdefault(name, {})

    def get_blob_client(self, path: str) -> _FakeBlobClient:
        return _FakeBlobClient(self._store, path)


class _FakeBlobService:
    def __init__(self) -> None:
        self.stores: dict[str, dict[str, bytes]] = {}

    def get_container_client(self, name: str) -> _FakeContainerClient:
        return _FakeContainerClient(name, self.stores)


class _FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.deleted: list[str] = []
        self._fail = fail

    async def delete(self, *keys: str) -> int:
        if self._fail:
            raise RuntimeError("redis down")
        self.deleted.extend(keys)
        return len(keys)


# ---- helpers ---------------------------------------------------------


def _settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _make_doc(
    *,
    skill_id: str = "flagged-skill",
    defender_status: str = "flagged",
    status: str = "classified",
    pinned: bool = False,
) -> SkillDoc:
    return SkillDoc(
        id=f"{skill_id}:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id=skill_id,
        version="1.0.0",
        name="Flagged Skill",
        description="",
        status=status,  # type: ignore[arg-type]
        classifier_status="done",
        uploader="alice@org",
        skill_md_text="# bad\n",
        pending_bundle_b64=base64.b64encode(b"# bad\n").decode("ascii"),
        defender_status=defender_status,  # type: ignore[arg-type]
        defender_severity="high",
        pinned=pinned,
        pinned_by="admin@org" if pinned else None,
    )


def _seed(skills: _FakeSkillsContainer, doc: SkillDoc) -> None:
    body = doc.model_dump(mode="json")
    body["_etag"] = '"etag-1"'
    skills.items[doc.id] = body


# ---- tests -----------------------------------------------------------


async def test_quarantine_happy_path_moves_bytes_and_flips_status():
    settings = _settings()
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    blob = _FakeBlobService()
    redis = _FakeRedis()
    doc = _make_doc()
    _seed(skills, doc)
    now = datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC)

    result = await quarantine_skill(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-1",
        justification="bundle exfiltrates env vars via curl",
        settings=settings,
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
        now=now,
    )

    assert result.status == "quarantined"
    assert result.quarantined_by == "admin@org"
    assert result.quarantine_justification == "bundle exfiltrates env vars via curl"
    assert result.quarantine_expires_at == now + timedelta(
        days=settings.quarantine_retention_days
    )
    assert result.pending_bundle_b64 is None

    # Blob written to quarantine container at the conventional path.
    q_store = blob.stores[settings.blob_quarantine_container]
    assert f"{doc.skill_id}/{doc.version}/bundle.tar.gz" in q_store
    assert q_store[f"{doc.skill_id}/{doc.version}/bundle.tar.gz"] == b"# bad\n"

    # Audit row recorded.
    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["action"] == "quarantine"
    assert row["actor"] == "admin@org"
    assert row["actor_oid"] == "oid-1"
    assert row["metadata"]["justification"] == "bundle exfiltrates env vars via curl"
    assert row["metadata"]["source"] == "admin_manual"
    assert row["metadata"]["retention_days"] == settings.quarantine_retention_days


async def test_quarantine_refuses_unflagged():
    skills = _FakeSkillsContainer()
    doc = _make_doc(defender_status="clean")
    _seed(skills, doc)

    with pytest.raises(DefenderNotFlagged):
        await quarantine_skill(
            skill_id=doc.skill_id,
            actor="admin@org",
            justification="long enough justification here",
            settings=_settings(),
            skills=skills,
            audit=_FakeAuditContainer(),
            blob=_FakeBlobService(),
            redis=_FakeRedis(),
        )


async def test_quarantine_refuses_short_justification():
    skills = _FakeSkillsContainer()
    doc = _make_doc()
    _seed(skills, doc)

    with pytest.raises(JustificationRequired):
        await quarantine_skill(
            skill_id=doc.skill_id,
            actor="admin@org",
            justification="bad",
            settings=_settings(),
            skills=skills,
            audit=_FakeAuditContainer(),
            blob=_FakeBlobService(),
            redis=_FakeRedis(),
        )


async def test_quarantine_refuses_pinned():
    skills = _FakeSkillsContainer()
    doc = _make_doc(pinned=True)
    _seed(skills, doc)

    with pytest.raises(SkillPinned):
        await quarantine_skill(
            skill_id=doc.skill_id,
            actor="admin@org",
            justification="long enough justification here",
            settings=_settings(),
            skills=skills,
            audit=_FakeAuditContainer(),
            blob=_FakeBlobService(),
            redis=_FakeRedis(),
        )


async def test_quarantine_skill_not_found():
    skills = _FakeSkillsContainer()
    with pytest.raises(SkillNotFound):
        await quarantine_skill(
            skill_id="nope",
            actor="admin@org",
            justification="long enough justification here",
            settings=_settings(),
            skills=skills,
            audit=_FakeAuditContainer(),
            blob=_FakeBlobService(),
            redis=_FakeRedis(),
        )


async def test_quarantine_swallows_redis_failure():
    """AGENTS.md §4 rule 2: cache miss is normal; failure is not fatal."""
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    blob = _FakeBlobService()
    redis = _FakeRedis(fail=True)
    doc = _make_doc()
    _seed(skills, doc)

    result = await quarantine_skill(
        skill_id=doc.skill_id,
        actor="admin@org",
        justification="long enough justification here",
        settings=_settings(),
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
    )
    assert result.status == "quarantined"
    assert len(audit.rows) == 1  # audit still recorded
