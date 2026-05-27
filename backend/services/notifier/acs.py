"""ACS email client (M5-5).

Two implementations selected by `Settings.notifier_provider`:

* `AzureAcsEmailClient` — production. Uses `azure-communication-email`'s
  async `EmailClient` with `DefaultAzureCredential` (Managed Identity in
  cloud, `az login` locally if exercised). The SDK is imported lazily
  inside `_build_sdk_client()` so the test path never needs the package.
* `FakeAcsEmailClient` — test double. Captures every `send()` call on
  `self.sent: list[AcsEmailMessage]`. Optionally raises if the caller
  pre-loaded `self._raise_with`.

The factory `make_acs_client(...)` picks the implementation off settings
so the worker stays oblivious to the choice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from backend.core.config import Settings

log = logging.getLogger(__name__)


@dataclass
class AcsEmailMessage:
    """Single email payload — what we hand to ACS (or capture in fakes)."""

    sender: str
    recipients: list[str]
    subject: str
    plain_text: str
    html: str
    # Optional message-id-style correlator the producer passes through
    # for log threading. Not an ACS field; only used by us in logs/tests.
    correlation_id: str = ""
    # Free-form headers / metadata; ACS accepts only a few of these but
    # the fake records them verbatim for assertions.
    headers: dict[str, str] = field(default_factory=dict)


class AcsEmailClient(Protocol):
    name: str

    async def send(self, message: AcsEmailMessage) -> str:
        """Send one email. Returns a provider-supplied message id (real
        client) or a synthetic id (fake). Raises on transport failure;
        the caller decides retry policy."""


# ----- Fake (tests + local dev) ---------------------------------------


class FakeAcsEmailClient:
    """In-memory ACS double. Records every send on `self.sent`.

    Used by unit tests, integration tests against the local emulator
    stack, and any local-dev demo where wiring a real ACS resource isn't
    worth the toil. Selection: `NOTIFIER_PROVIDER=fake` (the default).
    """

    name = "fake-acs-v1"

    def __init__(self) -> None:
        self.sent: list[AcsEmailMessage] = []
        # If non-None, the next `send()` call raises this instead of
        # recording. Lets tests exercise transport-failure code paths.
        self._raise_with: Exception | None = None

    def fail_next_with(self, exc: Exception) -> None:
        self._raise_with = exc

    async def send(self, message: AcsEmailMessage) -> str:
        if self._raise_with is not None:
            exc = self._raise_with
            self._raise_with = None
            raise exc
        self.sent.append(message)
        return f"fake-msg-{len(self.sent)}"


# ----- Real (azure-communication-email) -------------------------------


class AzureAcsEmailClient:
    """Production ACS client. Lazy SDK import keeps the test path clean.

    Auth precedence:
      1. `Settings.acs_connection_string` (Key Vault → env). This is the
         simplest path and what the chart provisions in M5-5.
      2. `DefaultAzureCredential` against `Settings.acs_endpoint`. The
         workload identity / managed identity must have the
         `Communication Services Contributor` role on the ACS resource.

    Sender address: `Settings.acs_sender_address`. Defaults to the
    managed-domain `DoNotReply@<random>.azurecomm.net` shape; operators
    flip to a verified custom domain in prod.
    """

    name = "azure-acs-v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None  # lazy

    def _build_sdk_client(self):  # noqa: ANN202 — SDK types not importable in test path
        # Lazy import — keeps the local-dev / unit-test path free of the
        # `azure-communication-email` dependency.
        try:
            from azure.communication.email.aio import EmailClient
        except ImportError as exc:  # pragma: no cover — exercised in prod build only
            raise RuntimeError(
                "azure-communication-email not installed. Add it to the "
                "notifier image's pyproject extras."
            ) from exc

        if self._settings.acs_connection_string:
            return EmailClient.from_connection_string(self._settings.acs_connection_string)
        if not self._settings.acs_endpoint:
            raise RuntimeError(
                "ACS configuration incomplete: set either ACS_CONNECTION_STRING "
                "or ACS_ENDPOINT (with workload identity granted the "
                "Communication Services Contributor role)."
            )
        from azure.identity.aio import DefaultAzureCredential

        return EmailClient(self._settings.acs_endpoint, DefaultAzureCredential())

    def _ensure_client(self):  # noqa: ANN202
        if self._client is None:
            self._client = self._build_sdk_client()
        return self._client

    async def send(self, message: AcsEmailMessage) -> str:  # pragma: no cover — needs ACS
        client = self._ensure_client()
        # ACS expects a specific dict shape; see
        # https://learn.microsoft.com/azure/communication-services/quickstarts/email/send-email
        body = {
            "senderAddress": message.sender,
            "recipients": {"to": [{"address": r} for r in message.recipients]},
            "content": {
                "subject": message.subject,
                "plainText": message.plain_text,
                "html": message.html,
            },
            "headers": message.headers or {},
        }
        poller = await client.begin_send(body)
        result = await poller.result()
        # SDK returns a dict-like with an `id` field.
        return getattr(result, "id", None) or result.get("id", "unknown")


# ----- factory --------------------------------------------------------


def make_acs_client(provider: str, *, settings: Settings | None = None) -> AcsEmailClient:
    """Pick a client. `fake` => `FakeAcsEmailClient`. `azure` => `AzureAcsEmailClient`."""
    if provider == "fake":
        return FakeAcsEmailClient()
    if provider == "azure":
        if settings is None:
            raise ValueError("make_acs_client('azure') requires settings=")
        return AzureAcsEmailClient(settings)
    raise ValueError(f"unknown notifier provider: {provider!r}")
