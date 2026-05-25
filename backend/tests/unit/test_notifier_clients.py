"""Notifier — model + ACS fake + Graph fake (M5-5)."""

from __future__ import annotations

from backend.models.notifications import (
    CONTRIBUTOR_EVENTS,
    NotificationEvent,
)
from backend.services.notifier import (
    FakeAcsEmailClient,
    FakeGraphClient,
    make_acs_client,
    make_graph_client,
)
from backend.services.notifier.acs import AcsEmailMessage
from backend.services.notifier.graph import AzureGraphClient

# ---- NotificationEvent ------------------------------------------------


def test_notification_event_defaults():
    ev = NotificationEvent(event_type="skill.uploaded", skill_id="s1")
    assert ev.payload == {}
    assert ev.idempotency_key == ""
    assert ev.event_id  # auto-generated
    assert not ev.is_contributor_event()


def test_notification_event_contributor_events_classified():
    ev = NotificationEvent(event_type="skill.approved", skill_id="s1", contributor_email="c@org")
    assert ev.is_contributor_event()
    assert ev.event_type in CONTRIBUTOR_EVENTS


def test_notification_event_ensure_idempotency_key_deterministic():
    """Two events with identical (type, skill_id, created_at) hash the same."""
    from datetime import UTC, datetime

    ts = datetime(2026, 1, 1, tzinfo=UTC)
    a = NotificationEvent(event_type="skill.uploaded", skill_id="s", created_at=ts)
    b = NotificationEvent(event_type="skill.uploaded", skill_id="s", created_at=ts)
    assert a.ensure_idempotency_key() == b.ensure_idempotency_key()
    # Once set, repeated calls return the same key.
    k = a.ensure_idempotency_key()
    assert a.ensure_idempotency_key() == k


def test_notification_event_explicit_idempotency_key_wins():
    ev = NotificationEvent(event_type="skill.uploaded", idempotency_key="explicit")
    assert ev.ensure_idempotency_key() == "explicit"


def test_supported_event_types_count_is_eight():
    """Plan §5 fixes the event vocabulary at exactly 8."""
    from backend.services.notifier import SUPPORTED_EVENT_TYPES

    assert len(SUPPORTED_EVENT_TYPES) == 8


# ---- Fake clients ----------------------------------------------------


async def test_fake_acs_captures_sends():
    client = FakeAcsEmailClient()
    msg = AcsEmailMessage(
        sender="from@x",
        recipients=["a@x"],
        subject="s",
        plain_text="t",
        html="<p>t</p>",
    )
    msg_id = await client.send(msg)
    assert msg_id.startswith("fake-msg-")
    assert client.sent == [msg]


async def test_fake_acs_can_simulate_failure():
    client = FakeAcsEmailClient()
    client.fail_next_with(RuntimeError("ACS down"))
    msg = AcsEmailMessage(
        sender="f", recipients=["r"], subject="s", plain_text="t", html="<p>t</p>"
    )
    import pytest

    with pytest.raises(RuntimeError, match="ACS down"):
        await client.send(msg)
    # Subsequent send works again.
    assert await client.send(msg) == "fake-msg-1"


async def test_fake_graph_returns_static_recipients_lowercased():
    g = FakeGraphClient(recipients=["Alice@Org", "BOB@Org"])
    out = await g.list_admin_recipients()
    assert out == ["alice@org", "bob@org"]
    assert g.calls == 1


async def test_fake_graph_default_recipients():
    g = FakeGraphClient()
    out = await g.list_admin_recipients()
    assert out == ["admin1@example.com", "admin2@example.com"]


def test_factory_picks_fake():
    assert isinstance(make_acs_client("fake"), FakeAcsEmailClient)
    assert isinstance(make_graph_client("fake"), FakeGraphClient)


def test_factory_rejects_unknown():
    import pytest

    with pytest.raises(ValueError):
        make_acs_client("nope")
    with pytest.raises(ValueError):
        make_graph_client("nope")


async def test_azure_graph_walks_all_pages(monkeypatch):
    from types import SimpleNamespace

    class _MembersBuilder:
        def __init__(self, pages):
            self._pages = pages
            self.urls: list[str] = []

        async def get(self):
            return self._pages[0]

        def with_url(self, url: str):
            self.urls.append(url)

            class _Next:
                async def get(_self):
                    return self._pages[1]

            return _Next()

    page1 = SimpleNamespace(
        value=[SimpleNamespace(mail="a@org", user_principal_name=None)],
        odata_next_link="next-page",
    )
    page2 = SimpleNamespace(
        value=[SimpleNamespace(mail=None, user_principal_name="B@Org")],
        odata_next_link=None,
    )
    members = _MembersBuilder([page1, page2])
    fake_client = SimpleNamespace(
        groups=SimpleNamespace(by_group_id=lambda _gid: SimpleNamespace(members=members))
    )
    settings = type(
        "S", (), {"entra_group_id_admin_notifications": "g", "entra_group_id_admin": ""}
    )()
    client = AzureGraphClient(settings)  # type: ignore[arg-type]
    monkeypatch.setattr(client, "_ensure_client", lambda: fake_client)

    assert await client.list_admin_recipients() == ["a@org", "b@org"]
    assert members.urls == ["next-page"]
