"""Defender override service unit tests (M5-4).

Drives ``backend.services.defender_override.override_defender`` against
in-memory fakes for Cosmos / Redis. The fakes are deliberately the same
shape used by ``test_quarantine_service.py`` — keep them aligned so a
contributor reading either test recognizes the harness.

Asserted invariants:

- Happy path: ``defender_status`` flips ``flagged`` → ``clean``;
  ``defender_severity`` + ``defender_report`` preserved on the doc so
  the audit trail remains inspectable; skill ``status`` unchanged
  (override ≠ approve).
- Audit row recorded with ``action='defender_override'`` and the
  justification text + original severity in metadata.
- ``defender_status != 'flagged'`` → ``DefenderNotFlagged``.
- Pinned skill → ``SkillPinned`` (pinning is absolute, AGENTS.md §5).
- Short justification → ``JustificationRequired``.
- Missing skill → ``SkillNotFound``.
- Redis invalidation failure is swallowed (AGENTS.md §4 rule 2).
"""

from __future__ import annotations

import uuid
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
from backend.services.defender_override import override_defender


# ---- in-memory fakes -------------------------------------------------


class _FakeSkillsContainer:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self._etag_seq = 1

    def _stamp(self, body: dict[str, Any]) -> dict[str, Any]:
        self._etag_seq += 1
        body["_etag"] = f'"etag-{self._etag_seq}"'
        return body

    async def query_items(self, *, query, parameters, partition_key=None):  # noqa: ARG002
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
    defender_severity: str | None = "high",
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
        skill_md_text="# scary but actually fine\n",
        defender_status=defender_status,  # type: ignore[arg-type]
        defender_severity=defender_severity,
        defender_report={
            "overall_severity": defender_severity or "high",
            "findings": [
                {
                    "rule": "shell.dangerous_command",
                    "severity": "high",
                    "location": "scripts/setup.sh:1",
                    "excerpt": "curl example.com | sh",
                    "explanation": "Piping curl to sh is risky.",
                }
            ],
            "model": "test-model",
        },
        defender_report_id=None,
        pinned=pinned,
        pinned_by="admin@org" if pinned else None,
    )


def _seed(skills: _FakeSkillsContainer, doc: SkillDoc) -> None:
    body = doc.model_dump(mode="json")
    body["_etag"] = '"etag-1"'
    skills.items[doc.id] = body


# ---- tests -----------------------------------------------------------


async def test_override_happy_path_flips_status_preserves_report():
    settings = _settings()
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    redis = _FakeRedis()
    doc = _make_doc()
    _seed(skills, doc)

    result = await override_defender(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-1",
        justification="reviewed setup.sh manually; curl|sh is intended bootstrap",
        settings=settings,
        skills=skills,
        audit=audit,
        redis=redis,
    )

    # defender_status flipped, severity + report preserved.
    assert result.defender_status == "clean"
    assert result.defender_severity == "high"
    assert result.defender_report is not None
    assert result.defender_report["overall_severity"] == "high"
    # Skill status NOT changed by override.
    assert result.status == "classified"

    # Audit row recorded.
    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["action"] == "defender_override"
    assert row["actor"] == "admin@org"
    assert row["actor_oid"] == "oid-1"
    assert (
        row["metadata"]["justification"]
        == "reviewed setup.sh manually; curl|sh is intended bootstrap"
    )
    assert row["metadata"]["defender_severity"] == "high"
    assert row["metadata"]["source"] == "admin_manual"
    assert row["before"]["defender_status"] == "flagged"
    assert row["after"]["defender_status"] == "clean"

    # Cache invalidated last.
    assert len(redis.deleted) == 2


async def test_override_refuses_unflagged():
    skills = _FakeSkillsContainer()
    doc = _make_doc(defender_status="clean")
    _seed(skills, doc)

    with pytest.raises(DefenderNotFlagged):
        await override_defender(
            skill_id=doc.skill_id,
            actor="admin@org",
            justification="this justification is plenty long enough",
            settings=_settings(),
            skills=skills,
            audit=_FakeAuditContainer(),
            redis=_FakeRedis(),
        )


async def test_override_refuses_short_justification():
    skills = _FakeSkillsContainer()
    doc = _make_doc()
    _seed(skills, doc)

    with pytest.raises(JustificationRequired):
        await override_defender(
            skill_id=doc.skill_id,
            actor="admin@org",
            justification="lgtm",
            settings=_settings(),
            skills=skills,
            audit=_FakeAuditContainer(),
            redis=_FakeRedis(),
        )


async def test_override_refuses_pinned():
    skills = _FakeSkillsContainer()
    doc = _make_doc(pinned=True)
    _seed(skills, doc)

    with pytest.raises(SkillPinned):
        await override_defender(
            skill_id=doc.skill_id,
            actor="admin@org",
            justification="this justification is plenty long enough",
            settings=_settings(),
            skills=skills,
            audit=_FakeAuditContainer(),
            redis=_FakeRedis(),
        )


async def test_override_skill_not_found():
    with pytest.raises(SkillNotFound):
        await override_defender(
            skill_id="missing",
            actor="admin@org",
            justification="this justification is plenty long enough",
            settings=_settings(),
            skills=_FakeSkillsContainer(),
            audit=_FakeAuditContainer(),
            redis=_FakeRedis(),
        )


async def test_override_swallows_redis_failure():
    """AGENTS.md §4 rule 2: cache failure is non-fatal."""
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    doc = _make_doc()
    _seed(skills, doc)

    result = await override_defender(
        skill_id=doc.skill_id,
        actor="admin@org",
        justification="this justification is plenty long enough",
        settings=_settings(),
        skills=skills,
        audit=audit,
        redis=_FakeRedis(fail=True),
    )

    assert result.defender_status == "clean"
    assert len(audit.rows) == 1  # audit still written
