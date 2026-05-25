from __future__ import annotations

import uuid
from typing import Any

import pytest

from backend.core.redis import key_queue_defender
from backend.models.skill import SkillDoc


class _FakeSkills:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def read_item(self, *, item: str, partition_key: str) -> dict[str, Any]:  # noqa: ARG002
        return self.items[item]

    async def replace_item(self, *, item: str, body: dict[str, Any]) -> dict[str, Any]:
        self.items[item] = dict(body)
        return self.items[item]


class _FakeAudit:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def create_item(self, *, body: dict[str, Any]) -> None:
        self.items.append(body)


class _FakeDB:
    def __init__(self, skills: _FakeSkills, audit: _FakeAudit) -> None:
        self._skills = skills
        self._audit = audit

    def get_container_client(self, name: str):
        if name == "skills":
            return self._skills
        if name == "audit":
            return self._audit
        raise KeyError(name)


class _FakeCosmos:
    def __init__(self, skills: _FakeSkills, audit: _FakeAudit) -> None:
        self._db = _FakeDB(skills, audit)

    def get_database_client(self, name: str):  # noqa: ARG002
        return self._db


class _FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.deleted: list[str] = []

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return len(keys)


def _doc(*, status: str) -> SkillDoc:
    return SkillDoc(
        id=f"approved-backfill:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id="approved-backfill",
        version="1.0.0",
        name="Approved Backfill",
        status=status,  # type: ignore[arg-type]
        classifier_status="queued",
        uploader="alice@org",
        skill_md_text="---\nname: approved-backfill\ncategory: devops\n---\n# Body\n",
        defender_status="pending",
    )


@pytest.mark.asyncio
async def test_classifier_backfills_approved_without_defender_enqueue(monkeypatch):
    from backend.core.config import Settings
    from backend.workers import classifier as worker

    monkeypatch.setattr(worker, "get_container", lambda db, name: db.get_container_client(name))
    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    doc = _doc(status="approved")
    skills.items[doc.id] = doc.model_dump(mode="json")

    await worker.process_one(
        doc_id=doc.id,
        cosmos_client=_FakeCosmos(skills, audit),
        redis=redis,
        settings=Settings(classifier_provider="stub"),  # type: ignore[arg-type]
    )

    updated = SkillDoc.model_validate(skills.items[doc.id])
    assert updated.status == "approved"
    assert updated.classifier_status == "done"
    assert updated.classification is not None
    assert key_queue_defender() not in redis.lists
