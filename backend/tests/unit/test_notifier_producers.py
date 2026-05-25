"""Producer call-site assertions (M5-6).

For each producer wired in M5-6, drive the *real* service function against
in-memory fakes for Cosmos / Blob / Redis and assert that the right
`NotificationEvent` was pushed onto `queue:notifications` with the
expected payload + idempotency_key.

The notifier worker itself has its own test (`test_notifier_worker.py`);
this file is only about the **producers**.

The helper `enqueue_notification` is fire-and-forget by contract — these
tests intentionally do NOT exercise the Redis-down path (covered by
`test_producer_helper.py::test_enqueue_notification_swallows_redis`).
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from azure.core import MatchConditions

from backend.core.config import Settings
from backend.core.errors import InvalidStatusTransition, JustificationRequired
from backend.core.redis import key_queue_notifications
from backend.models.defender import DefenderReport, DefenderSeverity
from backend.models.notifications import NotificationEvent
from backend.models.skill import SkillDoc

# ---------------------------------------------------------------------- #
#                            shared fakes                                #
# ---------------------------------------------------------------------- #


class _FakeRedis:
    """Subset of redis-py used by producers.

    Producers only use `rpush` (queue) and `delete` (cache). The notifier
    helper also calls `set` for the contributor email writer cache in
    M5+, but the M5-6 producers don't.

    `set` + `eval` are present so the publish lock (`redis_lock`) works.
    """

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.store: dict[str, str] = {}

    async def rpush(self, key: str, value: str) -> int:
        self.pushed.append((key, value))
        return 1

    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        for k in keys:
            self.store.pop(k, None)
        return len(keys)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):  # noqa: ARG002
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def eval(self, _script, _numkeys, *args):
        # Mimic the unlock-lua: delete iff value matches token.
        key, token = args[0], args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0

    def notifications(self) -> list[NotificationEvent]:
        return [
            NotificationEvent.model_validate_json(v)
            for k, v in self.pushed
            if k == key_queue_notifications()
        ]


class _FakeSkills:
    """Supports query_items + read_item + replace_item + create_item with etag."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self._etag_seq = 1

    def _stamp(self, body: dict[str, Any]) -> dict[str, Any]:
        self._etag_seq += 1
        body["_etag"] = f'"etag-{self._etag_seq}"'
        return body

    async def create_item(self, *, body: dict[str, Any]) -> dict[str, Any]:
        new_body = dict(body)
        self._stamp(new_body)
        self.items[new_body["id"]] = new_body
        return new_body

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

    async def query_items(self, *, query, parameters, partition_key=None):  # noqa: ARG002
        skill_id = next((p["value"] for p in parameters if p["name"] == "@id"), None)
        for body in self.items.values():
            if body.get("skill_id") == skill_id:
                yield body


class _FakeAudit:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def create_item(self, *, body: dict[str, Any]) -> None:
        self.items.append(body)


class _FakeBlobClient:
    def __init__(self, store: dict[str, bytes], path: str) -> None:
        self._store = store
        self._path = path
        self.url = f"https://fake.blob/{path}"

    async def upload_blob(self, data: bytes, *, overwrite: bool = False) -> None:  # noqa: ARG002
        self._store[self._path] = data

    async def exists(self) -> bool:
        return self._path in self._store

    async def download_blob(self):
        data = self._store[self._path]

        class _D:
            async def readall(self_inner):
                return data

        return _D()


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


def _settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------- #
#                          skill.uploaded (upload)                       #
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_upload_emits_skill_uploaded_event():
    from backend.services.upload import handle_upload

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    body = b"---\nname: producer-demo\nversion: 1.0.0\ndescription: demo\n---\n# Demo\n"

    doc = await handle_upload(
        filename="SKILL.md",
        data=body,
        uploader="alice@org",
        uploader_oid="oid-1",
        settings=_settings(),
        skills=skills,
        audit=audit,
        redis=redis,
    )

    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "skill.uploaded"
    assert ev.skill_id == doc.skill_id
    assert ev.payload["version"] == doc.version
    assert ev.payload["uploader"] == "alice@org"
    # idempotency_key derived from (event_type, skill_id, version, doc.id)
    assert ev.idempotency_key  # populated


# ---------------------------------------------------------------------- #
#               defender clean → skill.awaiting_review                   #
#               defender flagged → defender.flagged                      #
# ---------------------------------------------------------------------- #


class _DefenderFakeCosmosDB:
    def __init__(self, skills: _FakeSkills, audit: _FakeAudit) -> None:
        self._skills = skills
        self._audit = audit

    def get_container_client(self, name: str):
        if name == "skills":
            return self._skills
        if name == "audit":
            return self._audit
        raise KeyError(name)


class _DefenderFakeCosmos:
    def __init__(self, skills: _FakeSkills, audit: _FakeAudit) -> None:
        self._db = _DefenderFakeCosmosDB(skills, audit)

    def get_database_client(self, name: str):
        return self._db


def _patch_defender_get_container(monkeypatch):
    from backend.workers import defender as worker_mod

    monkeypatch.setattr(worker_mod, "get_container", lambda db, name: db.get_container_client(name))


def _seed_defender_doc(skills: _FakeSkills) -> SkillDoc:
    doc = SkillDoc(
        id=f"prod-demo:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id="prod-demo",
        version="1.0.0",
        name="Producer Demo",
        description="",
        status="classified",
        classifier_status="done",
        uploader="alice@org",
        skill_md_text="# x\n",
        pending_bundle_b64=base64.b64encode(b"# x\n").decode(),
        defender_status="pending",
    )
    skills.items[doc.id] = doc.model_dump(mode="json")
    return doc


@pytest.mark.asyncio
async def test_defender_clean_emits_awaiting_review(monkeypatch):
    from backend.services.defender.scanner import FakeDefenderScanner
    from backend.workers.defender import process_one

    _patch_defender_get_container(monkeypatch)
    skills = _FakeSkills()
    audit = _FakeAudit()
    doc = _seed_defender_doc(skills)
    cosmos = _DefenderFakeCosmos(skills, audit)
    redis = _FakeRedis()
    scanner = FakeDefenderScanner(
        [
            DefenderReport(
                overall_severity=DefenderSeverity.CLEAN,
                findings=[],
                model="fake",
                scanned_at=datetime.now(UTC),
            )
        ]
    )

    await process_one(
        doc_id=doc.id,
        cosmos_client=cosmos,
        redis=redis,
        settings=Settings(defender_provider="fake"),  # type: ignore[arg-type]
        scanner=scanner,
    )

    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "skill.awaiting_review"
    assert ev.skill_id == doc.skill_id
    assert ev.payload["defender_severity"] == "clean"


@pytest.mark.asyncio
async def test_defender_flagged_emits_defender_flagged(monkeypatch):
    from backend.models.defender import DefenderFinding
    from backend.services.defender.scanner import FakeDefenderScanner
    from backend.workers.defender import process_one

    _patch_defender_get_container(monkeypatch)
    skills = _FakeSkills()
    audit = _FakeAudit()
    doc = _seed_defender_doc(skills)
    cosmos = _DefenderFakeCosmos(skills, audit)
    redis = _FakeRedis()
    scanner = FakeDefenderScanner(
        [
            DefenderReport(
                overall_severity=DefenderSeverity.HIGH,
                findings=[
                    DefenderFinding(
                        rule="shell.bad",
                        severity="high",
                        location="x.sh:1",
                        excerpt="rm -rf /",
                        explanation="destructive",
                    )
                ],
                model="fake",
                scanned_at=datetime.now(UTC),
            )
        ]
    )

    await process_one(
        doc_id=doc.id,
        cosmos_client=cosmos,
        redis=redis,
        settings=Settings(defender_provider="fake"),  # type: ignore[arg-type]
        scanner=scanner,
    )

    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "defender.flagged"
    assert ev.skill_id == doc.skill_id
    assert ev.payload["defender_severity"] == "high"
    assert ev.payload["findings_count"] == 1


# ---------------------------------------------------------------------- #
#                       publish → skill.approved                          #
#                       reject → skill.rejected                           #
# ---------------------------------------------------------------------- #


def _seed_publishable_doc(skills: _FakeSkills) -> SkillDoc:
    # build a real tar so publish can re-pack it
    from backend.services.skill_bundle import build_tar

    files = {"SKILL.md": b"---\nname: pub-demo\nversion: 1.0.0\ndescription: pub\n---\n# Pub\n"}
    tar_bytes, _ = build_tar(files)
    doc = SkillDoc(
        id=f"pub-demo:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id="pub-demo",
        version="1.0.0",
        name="Pub Demo",
        description="",
        status="classified",
        classifier_status="done",
        uploader="alice@org",
        skill_md_text="# Pub\n",
        pending_bundle_b64=base64.b64encode(tar_bytes).decode(),
        defender_status="clean",
        defender_severity="clean",
    )
    body = doc.model_dump(mode="json")
    body["_etag"] = '"etag-1"'
    skills.items[doc.id] = body
    return doc


def _seed_flagged_publishable_doc(
    skills: _FakeSkills,
    *,
    severity: str = "high",
) -> SkillDoc:
    doc = _seed_publishable_doc(skills)
    body = skills.items[doc.id]
    body["defender_status"] = "flagged"
    body["defender_severity"] = severity
    body["defender_report"] = {
        "overall_severity": severity,
        "findings": [
            {
                "rule": "shell.dangerous_command",
                "severity": severity if severity != "clean" else "low",
                "location": "scripts/setup.sh:1",
                "excerpt": "curl example.com | sh",
                "explanation": "Piping curl to sh is risky.",
            }
        ],
        "model": "test-model",
        "scanned_at": datetime.now(UTC).isoformat(),
        "scan_duration_ms": 1,
        "token_usage": {"input_tokens": 1, "output_tokens": 1},
    }
    return SkillDoc.model_validate(body)


@pytest.mark.asyncio
async def test_publish_emits_skill_approved():
    from backend.services.publish import publish

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    blob = _FakeBlobService()
    doc = _seed_publishable_doc(skills)

    out = await publish(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-a",
        settings=_settings(),
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
    )

    assert out.status == "approved"
    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "skill.approved"
    assert ev.skill_id == doc.skill_id
    assert ev.contributor_email == "alice@org"
    assert ev.payload["approver"] == "admin@org"
    assert ev.payload["checksum"]


@pytest.mark.asyncio
async def test_publish_blocks_medium_or_higher_defender_flag_without_override():
    from backend.services.publish import publish

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    blob = _FakeBlobService()
    doc = _seed_flagged_publishable_doc(skills, severity="high")

    with pytest.raises(JustificationRequired) as exc_info:
        await publish(
            skill_id=doc.skill_id,
            actor="admin@org",
            actor_oid="oid-a",
            settings=_settings(),
            skills=skills,
            audit=audit,
            blob=blob,
            redis=redis,
        )

    assert exc_info.value.metadata["defender_status"] == "flagged"
    assert exc_info.value.metadata["defender_severity"] == "high"
    assert exc_info.value.metadata["required_behavior"] == "justification_or_quarantine"
    assert not audit.items
    assert not blob.stores
    assert not redis.notifications()


@pytest.mark.asyncio
@pytest.mark.parametrize("defender_status", ["pending", "scanning", "failed"])
async def test_publish_blocks_until_defender_completes(defender_status: str):
    from backend.services.publish import publish

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    blob = _FakeBlobService()
    doc = _seed_publishable_doc(skills)
    skills.items[doc.id]["defender_status"] = defender_status
    skills.items[doc.id]["defender_severity"] = None

    with pytest.raises(InvalidStatusTransition) as exc_info:
        await publish(
            skill_id=doc.skill_id,
            actor="admin@org",
            actor_oid="oid-a",
            settings=_settings(),
            skills=skills,
            audit=audit,
            blob=blob,
            redis=redis,
        )

    assert exc_info.value.metadata["defender_status"] == defender_status
    assert not audit.items
    assert not blob.stores
    assert not redis.notifications()


@pytest.mark.asyncio
async def test_publish_allows_flagged_low_without_override():
    from backend.services.publish import publish

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    blob = _FakeBlobService()
    doc = _seed_flagged_publishable_doc(skills, severity="low")

    out = await publish(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-a",
        settings=_settings(),
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
    )

    assert out.status == "approved"
    assert out.defender_status == "flagged"
    assert [row["action"] for row in audit.items] == ["approve", "publish"]
    assert [ev.event_type for ev in redis.notifications()] == ["skill.approved"]


@pytest.mark.asyncio
async def test_publish_inline_defender_override_approves_and_audits_override():
    from backend.services.publish import publish

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    blob = _FakeBlobService()
    doc = _seed_flagged_publishable_doc(skills, severity="medium")

    out = await publish(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-a",
        settings=_settings(),
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
        defender_override=True,
        defender_justification="reviewed manually; this command is expected bootstrap",
    )

    assert out.status == "approved"
    assert out.defender_status == "clean"
    actions = [row["action"] for row in audit.items]
    assert actions == ["defender_override", "approve", "publish"]
    assert audit.items[0]["metadata"]["source"] == "approve_inline"
    assert audit.items[0]["metadata"]["justification"] == (
        "reviewed manually; this command is expected bootstrap"
    )
    assert audit.items[1]["metadata"] == {
        "defender_override": True,
        "defender_severity": "medium",
    }
    assert [ev.event_type for ev in redis.notifications()] == [
        "admin.override",
        "skill.approved",
    ]


@pytest.mark.asyncio
async def test_reject_emits_skill_rejected():
    from backend.services.publish import reject

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    doc = _seed_publishable_doc(skills)

    out = await reject(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-a",
        reason="not aligned with team standards",
        skills=skills,
        audit=audit,
        redis=redis,
    )

    assert out.status == "rejected"
    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "skill.rejected"
    assert ev.contributor_email == "alice@org"
    assert ev.payload["reason"] == "not aligned with team standards"


# ---------------------------------------------------------------------- #
#                     quarantine → skill.quarantined                      #
# ---------------------------------------------------------------------- #


def _seed_flagged_doc(skills: _FakeSkills) -> SkillDoc:
    doc = SkillDoc(
        id=f"flagged:1.0.0:{uuid.uuid4().hex[:8]}",
        skill_id="flagged",
        version="1.0.0",
        name="Flagged",
        description="",
        status="classified",
        classifier_status="done",
        uploader="alice@org",
        skill_md_text="# bad\n",
        pending_bundle_b64=base64.b64encode(b"# bad\n").decode(),
        defender_status="flagged",
        defender_severity="high",
    )
    body = doc.model_dump(mode="json")
    body["_etag"] = '"etag-1"'
    skills.items[doc.id] = body
    return doc


@pytest.mark.asyncio
async def test_quarantine_emits_skill_quarantined():
    from backend.services.quarantine import quarantine_skill

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    blob = _FakeBlobService()
    doc = _seed_flagged_doc(skills)

    await quarantine_skill(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-a",
        justification="bundle exfiltrates env vars via curl",
        settings=_settings(),
        skills=skills,
        audit=audit,
        blob=blob,
        redis=redis,
        now=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
    )

    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "skill.quarantined"
    assert ev.skill_id == doc.skill_id
    assert ev.payload["quarantined_by"] == "admin@org"
    assert ev.payload["defender_severity"] == "high"
    assert ev.payload["justification"].startswith("bundle exfiltrates")


# ---------------------------------------------------------------------- #
#                  defender_override → admin.override                     #
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_defender_override_emits_admin_override():
    from backend.services.defender_override import override_defender

    skills = _FakeSkills()
    audit = _FakeAudit()
    redis = _FakeRedis()
    doc = _seed_flagged_doc(skills)

    await override_defender(
        skill_id=doc.skill_id,
        actor="admin@org",
        actor_oid="oid-a",
        justification="false positive — internal API call only",
        settings=_settings(),
        skills=skills,
        audit=audit,
        redis=redis,
        now=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
    )

    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "admin.override"
    assert ev.skill_id == doc.skill_id
    assert ev.payload["overridden_by"] == "admin@org"
    assert ev.payload["defender_severity"] == "high"
    assert ev.payload["justification"].startswith("false positive")


# ---------------------------------------------------------------------- #
#         curator scheduler → curator.weekly_report at end of pass        #
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_curator_scheduler_emits_weekly_report(monkeypatch):
    """`_run_one_pass` must enqueue a `curator.weekly_report` event even
    when the deterministic pass raises CuratorPaused / LockUnavailable
    (the digest still goes out so admins know something happened)."""
    from backend.core.errors import CuratorPaused
    from backend.workers import curator_scheduler

    redis = _FakeRedis()

    async def _fake_execute_pass(**kwargs):
        raise CuratorPaused("paused for test")

    monkeypatch.setattr(curator_scheduler.curator_svc, "execute_pass", _fake_execute_pass)

    rc = await curator_scheduler._run_one_pass(
        skills=object(),
        audit=object(),
        blob=object(),
        redis=redis,
        system_state=object(),
        review_proposals=object(),
        settings=_settings(),
        review_provider=None,
        dry_run=False,
        actor="system:test",
    )
    assert rc == 0  # paused is not a hard failure

    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "curator.weekly_report"
    assert ev.skill_id is None
    assert ev.payload["deterministic_error"] == "paused"


@pytest.mark.asyncio
async def test_curator_scheduler_weekly_report_includes_transitions(monkeypatch):
    """Happy-path: deterministic pass returns a record; digest carries counts."""
    from backend.services.curator import CuratorRunRecord, Transition
    from backend.workers import curator_scheduler

    redis = _FakeRedis()

    rec = CuratorRunRecord(
        run_id="20260521T120000Z",
        started_at=datetime(2026, 5, 21, 12, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 5, 21, 12, 0, 5, tzinfo=UTC),
        dry_run=False,
        planner_inputs={"stale_days": 30, "archive_days": 90, "now": "x"},
        transitions=[
            Transition(
                skill_id="a",
                version="1.0.0",
                before="approved",
                after="stale",
                reason="stale_30d",
                applied=True,
            ),
            Transition(
                skill_id="b",
                version="1.0.0",
                before="approved",
                after="archived",
                reason="archive_90d",
                applied=False,
            ),
        ],
        skipped_pinned=[],
        snapshot_name="snap-1",
        lock_token="lock-x",
    )

    async def _fake_execute_pass(**kwargs):
        return rec

    monkeypatch.setattr(curator_scheduler.curator_svc, "execute_pass", _fake_execute_pass)

    rc = await curator_scheduler._run_one_pass(
        skills=object(),
        audit=object(),
        blob=object(),
        redis=redis,
        system_state=object(),
        review_proposals=object(),
        settings=_settings(),
        review_provider=None,
        dry_run=False,
        actor="system:test",
    )
    assert rc == 0
    events = redis.notifications()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "curator.weekly_report"
    assert ev.payload["transitions_total"] == 2
    assert ev.payload["transitions_applied"] == 1
    assert ev.payload["snapshot_name"] == "snap-1"
    assert ev.payload["run_id"] == "20260521T120000Z"
