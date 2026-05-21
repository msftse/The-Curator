"""Notifier service package (M5-5).

Three submodules:

* `acs`       — Azure Communication Services email client. Real client
                uses `azure-communication-email` + `DefaultAzureCredential`
                (Managed Identity in cloud). Fake client captures sends
                in memory for tests.
* `graph`     — Microsoft Graph client for admin-recipient resolution
                from an Entra security group. Fake returns a static list
                so local-dev needs no Graph admin consent.
* `templates/`— Plaintext + HTML body per event type. Rendered with
                `str.format(**payload)` — zero new dependencies.

The worker (`backend/workers/notifier.py`) is the only caller. No
Cosmos / Blob writes happen here; Redis is touched only by the worker
itself (idempotency `SETNX`, recipient cache, etc.). Listed in the AST
never-delete gate.
"""

from __future__ import annotations

from backend.services.notifier.acs import (
    AcsEmailClient,
    AcsEmailMessage,
    FakeAcsEmailClient,
    make_acs_client,
)
from backend.services.notifier.graph import (
    FakeGraphClient,
    GraphClient,
    make_graph_client,
)
from backend.services.notifier.templates import (
    SUPPORTED_EVENT_TYPES,
    render_template,
)

__all__ = [
    "SUPPORTED_EVENT_TYPES",
    "AcsEmailClient",
    "AcsEmailMessage",
    "FakeAcsEmailClient",
    "FakeGraphClient",
    "GraphClient",
    "make_acs_client",
    "make_graph_client",
    "render_template",
]
