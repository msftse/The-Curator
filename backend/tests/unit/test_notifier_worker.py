"""Notifier worker — process_one + dedupe + recipient resolution + audit (M5-5)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.core.config import Settings
from backend.core.redis import key_admin_recipients, key_notif_sent
from backend.models.notifications import NotificationEvent
from backend.services.notifier import FakeAcsEmailClient, FakeGraphClient
from backend.workers.notifier import process_one

# ---- in-memory fakes -------------------------------------------------


class _FakeAuditContainer:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def create_item(self, *, body: dict[str, Any]) -> None:
        self.items.append(body)


class _FakeDB:
    def __init__(self, audit: _FakeAuditContainer) -> None:
        self._audit = audit

    def get_container_client(self, name: str) -> _FakeAuditContainer:
        if name == "audit":
            return self._audit
        raise KeyError(name)


class _FakeCosmos:
    def __init__(self, audit: _FakeAuditContainer) -> None:
        self._db = _FakeDB(audit)

    def get_database_client(self, name: str) -> _FakeDB:
        return self._db


class _FakeRedis:
    """In-memory subset of redis-py we need: get/set with nx/ex, no TTL enforcement."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        self.set_calls.append((key, value, {"nx": nx, "ex": ex}))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


def _patch_get_container(monkeypatch):
    from backend.workers import notifier as worker_mod

    def _direct(db, name):
        return db.get_container_client(name)

    monkeypatch.setattr(worker_mod, "get_container", _direct)


def _settings() -> Settings:
    return Settings(  # type: ignore[arg-type]
        notifier_provider="fake",
        notifier_graph_provider="fake",
        acs_sender_address="DoNotReply@test.local",
    )


# ---- happy path ------------------------------------------------------


async def test_process_one_admin_event_sends_and_audits(monkeypatch):
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient(recipients=["a@org", "b@org"])

    ev = NotificationEvent(
        event_type="skill.uploaded",
        skill_id="my-skill",
        payload={
            "skill_name": "My Skill",
            "skill_id": "my-skill",
            "version": "1.0.0",
            "uploader": "alice@org",
            "uploaded_at": "now",
        },
        idempotency_key="ev-1",
    )

    await process_one(
        raw=ev.model_dump_json(),
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        acs=acs,
        graph=graph,
    )

    # ACS got the send with the resolved admin list.
    assert len(acs.sent) == 1
    msg = acs.sent[0]
    assert msg.recipients == ["a@org", "b@org"]
    assert "My Skill" in msg.subject
    assert "alice@org" in msg.plain_text

    # Audit row recorded.
    assert len(audit.items) == 1
    assert audit.items[0]["metadata"]["event_type"] == "skill.uploaded"
    assert audit.items[0]["metadata"]["provider_message_id"] == "fake-msg-1"

    # Dedupe key was set.
    assert key_notif_sent("ev-1") in redis.store
    # Recipient cache was populated.
    assert key_admin_recipients() in redis.store


async def test_process_one_contributor_event_addresses_uploader(monkeypatch):
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient()

    ev = NotificationEvent(
        event_type="skill.approved",
        skill_id="my-skill",
        contributor_email="alice@org",
        payload={
            "skill_name": "My Skill",
            "skill_id": "my-skill",
            "version": "1.0.0",
            "actor": "admin@org",
            "published_at": "now",
        },
        idempotency_key="approved-1",
    )
    await process_one(
        raw=ev.model_dump_json(),
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        acs=acs,
        graph=graph,
    )
    assert acs.sent[0].recipients == ["alice@org"]
    # Graph not consulted for contributor events.
    assert graph.calls == 0


# ---- dedupe ----------------------------------------------------------


async def test_process_one_deduplicates_by_idempotency_key(monkeypatch):
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient()

    ev = NotificationEvent(
        event_type="skill.uploaded",
        skill_id="x",
        payload={"skill_name": "X"},
        idempotency_key="dupe-key",
    )
    raw = ev.model_dump_json()

    await process_one(
        raw=raw, cosmos_client=cosmos, redis=redis, settings=_settings(), acs=acs, graph=graph
    )
    await process_one(
        raw=raw, cosmos_client=cosmos, redis=redis, settings=_settings(), acs=acs, graph=graph
    )

    # Only the first send went through.
    assert len(acs.sent) == 1
    # Audit only recorded the first attempt.
    assert len(audit.items) == 1


# ---- failure modes ---------------------------------------------------


async def test_process_one_send_failure_still_audits(monkeypatch):
    """ACS failure is logged + audited as an attempt; we don't crash the loop."""
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    acs.fail_next_with(RuntimeError("ACS 500"))
    graph = FakeGraphClient()

    ev = NotificationEvent(event_type="skill.uploaded", skill_id="x", idempotency_key="k")
    await process_one(
        raw=ev.model_dump_json(),
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        acs=acs,
        graph=graph,
    )
    assert acs.sent == []  # send raised
    assert len(audit.items) == 1
    assert audit.items[0]["metadata"]["error"] == "ACS 500"
    assert audit.items[0]["metadata"]["provider_message_id"] is None


async def test_process_one_unknown_event_type_skipped(monkeypatch):
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient()

    # Bypass the model's Literal by hand-rolling the JSON.
    raw = '{"event_type": "not.a.real.event", "skill_id": "x"}'
    await process_one(
        raw=raw, cosmos_client=cosmos, redis=redis, settings=_settings(), acs=acs, graph=graph
    )
    assert acs.sent == []
    assert audit.items == []


async def test_process_one_unparseable_event_skipped(monkeypatch):
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient()

    await process_one(
        raw="not-json",
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        acs=acs,
        graph=graph,
    )
    assert acs.sent == []


async def test_process_one_no_recipients_skips_send(monkeypatch):
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient(recipients=[])

    ev = NotificationEvent(event_type="skill.uploaded", skill_id="x", idempotency_key="k")
    await process_one(
        raw=ev.model_dump_json(),
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        acs=acs,
        graph=graph,
    )
    assert acs.sent == []


async def test_process_one_contributor_event_missing_email_skips(monkeypatch):
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient()

    ev = NotificationEvent(event_type="skill.approved", skill_id="x", idempotency_key="k")
    # No contributor_email — must NOT fall through to admins.
    await process_one(
        raw=ev.model_dump_json(),
        cosmos_client=cosmos,
        redis=redis,
        settings=_settings(),
        acs=acs,
        graph=graph,
    )
    assert acs.sent == []


# ---- recipient cache -------------------------------------------------


async def test_resolve_recipients_uses_redis_cache(monkeypatch):
    """Second admin event should hit the cache, not Graph."""
    _patch_get_container(monkeypatch)
    audit = _FakeAuditContainer()
    cosmos = _FakeCosmos(audit)
    redis = _FakeRedis()
    acs = FakeAcsEmailClient()
    graph = FakeGraphClient(recipients=["a@org"])

    for i in range(2):
        ev = NotificationEvent(
            event_type="skill.uploaded",
            skill_id=f"s{i}",
            idempotency_key=f"k{i}",
        )
        await process_one(
            raw=ev.model_dump_json(),
            cosmos_client=cosmos,
            redis=redis,
            settings=_settings(),
            acs=acs,
            graph=graph,
        )
    assert len(acs.sent) == 2
    # Graph called only once — second event read from cache.
    assert graph.calls == 1


# ---- BLPOP loop wiring ----------------------------------------------


async def test_run_loop_stops_on_event(monkeypatch):
    """The BLPOP loop honors a pre-set stop event without ever blocking on Redis."""
    import asyncio

    import backend.workers.notifier as nmod

    stop = asyncio.Event()
    stop.set()  # pre-set so the loop exits before first iteration

    # Stub out everything that talks to the outside world. The loop should
    # only check `stop.is_set()` and return.
    monkeypatch.setattr(nmod, "get_cosmos_client", lambda s: _FakeCosmos(_FakeAuditContainer()))
    monkeypatch.setattr(nmod, "ensure_containers", _aio_noop)

    class _DummyRedis:
        async def blpop(self, *a, **kw):
            pytest.fail("blpop should not be called when stop is pre-set")

        async def aclose(self):
            pass

    monkeypatch.setattr("backend.core.redis.get_redis", lambda s: _DummyRedis())
    monkeypatch.setattr(nmod, "make_acs_client", lambda *a, **kw: FakeAcsEmailClient())
    monkeypatch.setattr(nmod, "make_graph_client", lambda *a, **kw: FakeGraphClient())

    await nmod.run_loop(stop)


async def _aio_noop(*a, **kw):
    return None
