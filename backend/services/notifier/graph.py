"""Microsoft Graph admin recipient resolution (M5-5).

Resolves the set of admin email addresses by enumerating an Entra
security group's members. Two implementations:

* `FakeGraphClient` — returns a static list (default
  `["admin1@example.com", "admin2@example.com"]`). Used by unit tests
  and local-dev so contributors don't need to admin-consent the
  `GroupMember.Read.All` application permission.
* `AzureGraphClient` — production. Uses the official `msgraph-sdk`
  with `DefaultAzureCredential`. The notifier UAMI must have the
  `GroupMember.Read.All` application permission, tenant-admin
  consented. Documented in `scripts/setup-entra.sh` (and AGENTS.md
  §13).

The notifier worker caches the resolved list in Redis for 15 minutes
(key `admin:recipients`) so we don't pay Graph latency on every event.
"""

from __future__ import annotations

import logging
from typing import Protocol

from backend.core.config import Settings

log = logging.getLogger(__name__)


class GraphClient(Protocol):
    name: str

    async def list_admin_recipients(self) -> list[str]:
        """Return admin email addresses (lower-cased, de-duped)."""


# ----- Fake (tests + local dev) ---------------------------------------


class FakeGraphClient:
    """Static admin list — no Graph round trip.

    The default list is intentionally obvious so accidentally enabling
    the fake in prod surfaces in the audit trail / inbox immediately.
    """

    name = "fake-graph-v1"

    def __init__(self, recipients: list[str] | None = None) -> None:
        # `None` => use defaults; `[]` => explicitly no recipients (tests
        # exercising the "admin group is empty" code path rely on this).
        if recipients is None:
            self._recipients = ["admin1@example.com", "admin2@example.com"]
        else:
            self._recipients = list(recipients)
        self.calls = 0

    async def list_admin_recipients(self) -> list[str]:
        self.calls += 1
        return [r.lower() for r in self._recipients]


# ----- Real (msgraph-sdk) ---------------------------------------------


class AzureGraphClient:
    """Production Graph client. Lazy SDK import.

    Resolves `Settings.entra_group_id_admin_notifications` (falls back
    to `Settings.entra_group_id_admin` if the notifications-specific
    group isn't set). Filters out members whose `mail` field is empty
    (guest accounts, service principals, etc.).
    """

    name = "azure-graph-v1"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None  # lazy

    def _build_client(self):  # noqa: ANN202 — SDK types not importable in test path
        try:
            from azure.identity.aio import DefaultAzureCredential
            from msgraph import GraphServiceClient
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "msgraph-sdk not installed. Add it to the notifier image's pyproject extras."
            ) from exc

        scopes = ["https://graph.microsoft.com/.default"]
        return GraphServiceClient(credentials=DefaultAzureCredential(), scopes=scopes)

    def _ensure_client(self):  # noqa: ANN202
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _group_id(self) -> str:
        return (
            self._settings.entra_group_id_admin_notifications or self._settings.entra_group_id_admin
        )

    async def list_admin_recipients(self) -> list[str]:  # pragma: no cover — needs Graph
        gid = self._group_id()
        if not gid:
            log.warning("graph.admin_group_not_configured")
            return []
        client = self._ensure_client()
        out: list[str] = []
        request_builder = client.groups.by_group_id(gid).members
        page = await request_builder.get()
        while page is not None:
            for m in getattr(page, "value", []) or []:
                email = getattr(m, "mail", None) or getattr(m, "user_principal_name", None)
                if email:
                    out.append(email.lower())
            next_link = getattr(page, "odata_next_link", None)
            if not next_link:
                break
            page = await client.groups.by_group_id(gid).members.with_url(next_link).get()
        # De-dupe while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for e in out:
            if e not in seen:
                seen.add(e)
                deduped.append(e)
        return deduped


# ----- factory --------------------------------------------------------


def make_graph_client(provider: str, *, settings: Settings | None = None) -> GraphClient:
    if provider == "fake":
        return FakeGraphClient()
    if provider == "azure":
        if settings is None:
            raise ValueError("make_graph_client('azure') requires settings=")
        return AzureGraphClient(settings)
    raise ValueError(f"unknown graph provider: {provider!r}")
