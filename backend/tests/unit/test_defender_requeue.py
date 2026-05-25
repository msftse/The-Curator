from __future__ import annotations

import uuid
from typing import Any

import pytest
from azure.core import MatchConditions

from backend.core.errors import InvalidStatusTransition
from backend.core.redis import key_queue_defender
from backend.models.skill import Bundle, SkillDoc
from backend.services.defender_requeue import requeue_defender


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

                raise cosmos_exc.CosmosAccessConditionFailedError(status_code=412, message="etag")
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


def _doc(*, status: str = "approved") -> SkillDoc:
    return SkillDoc(
        id=f"rescan:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id="rescan",
        version="1.0.0",
        name="Rescan",
        status=status,  # type: ignore[arg-type]
        classifier_status="done",
        uploader="alice@org",
        defender_status="clean",
        defender_severity="clean",
        defender_report={"overall_severity": "clean", "findings": [], "model": "fake-v1"},
        bundle=Bundle(blob_url="https://fake", checksum_sha256="x", size_bytes=1, file_count=1),
    )


def _seed(skills: _FakeSkillsContainer, doc: SkillDoc) -> None:
    body = doc.model_dump(mode="json")
    body["_etag"] = '"etag-1"'
    skills.items[doc.id] = body


@pytest.mark.asyncio
async def test_requeue_defender_clears_old_report_and_pushes_queue():
    skills = _FakeSkillsContainer()
    audit = _FakeAuditContainer()
    redis = _FakeRedis()
    doc = _doc()
    _seed(skills, doc)

    result = await requeue_defender(
        skill_id=doc.skill_id,
        actor="admin@org",
        skills=skills,
        audit=audit,
        redis=redis,
    )

    assert result.defender_status == "pending"
    assert result.defender_severity is None
    assert result.defender_report is None
    assert redis.lists[key_queue_defender()] == [doc.id]
    assert audit.rows[0]["metadata"]["source"] == "admin_rescan"


@pytest.mark.asyncio
async def test_requeue_defender_refuses_quarantined():
    skills = _FakeSkillsContainer()
    doc = _doc(status="quarantined")
    _seed(skills, doc)

    with pytest.raises(InvalidStatusTransition):
        await requeue_defender(
            skill_id=doc.skill_id,
            actor="admin@org",
            skills=skills,
            audit=_FakeAuditContainer(),
            redis=_FakeRedis(),
        )
