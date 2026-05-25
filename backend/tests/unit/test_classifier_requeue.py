from __future__ import annotations

import uuid
from typing import Any

import pytest
from azure.core import MatchConditions

from backend.core.errors import InvalidStatusTransition, SkillNotFound
from backend.core.redis import key_queue_classifier
from backend.models.skill import SkillDoc
from backend.services.classifier_requeue import requeue_classifier


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
                    status_code=412,
                    message="etag mismatch",
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
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.deleted: list[str] = []

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return len(keys)


def _make_doc(
    *,
    skill_id: str = "needs-classify",
    status: str = "pending",
    classifier_status: str = "failed",
) -> SkillDoc:
    return SkillDoc(
        id=f"{skill_id}:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id=skill_id,
        version="1.0.0",
        name="Needs Classify",
        status=status,  # type: ignore[arg-type]
        classifier_status=classifier_status,  # type: ignore[arg-type]
        uploader="alice@org",
        skill_md_text="# Demo\n",
        pending_bundle_b64="ZmFrZQ==",
    )


def _seed(skills: _FakeSkillsContainer, doc: SkillDoc) -> None:
    body = doc.model_dump(mode="json")
    body["_etag"] = '"etag-1"'
    skills.items[doc.id] = body


@pytest.mark.asyncio
async def test_requeue_classifier_marks_queued_and_pushes_queue():
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    redis = _FakeRedis()
    doc = _make_doc(classifier_status="failed")
    _seed(skills, doc)

    result = await requeue_classifier(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-1",
        skills=skills,
        audit=audit,
        redis=redis,
    )

    assert result.classifier_status == "queued"
    assert redis.lists[key_queue_classifier()] == [doc.id]
    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row["action"] == "classify"
    assert row["actor"] == "admin@org"
    assert row["metadata"]["source"] == "admin_requeue"
    assert row["metadata"]["doc_id"] == doc.id


@pytest.mark.asyncio
async def test_requeue_classifier_refuses_terminal_status():
    skills = _FakeSkillsContainer()
    doc = _make_doc(status="quarantined")
    _seed(skills, doc)

    with pytest.raises(InvalidStatusTransition):
        await requeue_classifier(
            skill_id=doc.skill_id,
            actor="admin@org",
            skills=skills,
            audit=_FakeAuditContainer(),
            redis=_FakeRedis(),
        )


@pytest.mark.asyncio
async def test_requeue_classifier_allows_approved_backfill():
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    redis = _FakeRedis()
    doc = _make_doc(status="approved", classifier_status="failed")
    _seed(skills, doc)

    result = await requeue_classifier(
        skill_id=doc.skill_id,
        actor="admin@org",
        skills=skills,
        audit=audit,
        redis=redis,
    )

    assert result.status == "approved"
    assert result.classifier_status == "queued"
    assert redis.lists[key_queue_classifier()] == [doc.id]


@pytest.mark.asyncio
async def test_requeue_classifier_missing_skill():
    with pytest.raises(SkillNotFound):
        await requeue_classifier(
            skill_id="missing",
            actor="admin@org",
            skills=_FakeSkillsContainer(),
            audit=_FakeAuditContainer(),
            redis=_FakeRedis(),
        )
