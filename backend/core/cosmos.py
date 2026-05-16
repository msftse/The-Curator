"""Async Cosmos DB client + idempotent container bootstrap.

Cosmos is the system of record (AGENTS.md §3). All durable writes hit Cosmos
first. Containers are created on app startup so the worker and tests can
assume they exist.

Auth: when `COSMOS_KEY` is set we authenticate with the master key (used by
the emulator and by accounts that still have local auth enabled). When the
key is empty we fall back to `DefaultAzureCredential`, which picks up
`az login` locally and Managed Identity in Azure. The account must have a
Cosmos DB data-plane role assignment (e.g. "Cosmos DB Built-in Data
Contributor") for the calling principal.
"""

from __future__ import annotations

import urllib3
from azure.cosmos import PartitionKey
from azure.cosmos.aio import ContainerProxy, CosmosClient, DatabaseProxy
from azure.identity.aio import DefaultAzureCredential

from backend.core.config import Settings

# Cosmos containers per PRD §10.
SKILLS_CONTAINER = "skills"
AUDIT_CONTAINER = "audit"
USAGE_EVENTS_CONTAINER = "usage_events"
API_KEYS_CONTAINER = "api_keys"
SYSTEM_STATE_CONTAINER = "system_state"
REVIEW_PROPOSALS_CONTAINER = "review_proposals"
USAGE_EVENTS_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days


def get_cosmos_client(settings: Settings) -> CosmosClient:
    """Build an async Cosmos client.

    With the local emulator + COSMOS_VERIFY_TLS=false we have to silence the
    InsecureRequestWarning the SDK emits on every call. This is a dev-only
    shortcut documented in AGENTS.md and the env example.

    If `COSMOS_KEY` is empty, authenticate with `DefaultAzureCredential`
    (passwordless / `az login` / Managed Identity). The data-plane role
    `Cosmos DB Built-in Data Contributor` (or stronger) is required for the
    signed-in principal — control-plane RBAC alone is NOT enough.
    """
    connection_verify = settings.cosmos_verify_tls
    if not connection_verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    credential: str | DefaultAzureCredential = settings.cosmos_key or DefaultAzureCredential()

    return CosmosClient(
        url=settings.cosmos_endpoint,
        credential=credential,
        connection_verify=connection_verify,
    )


async def ensure_containers(client: CosmosClient, db_name: str) -> DatabaseProxy:
    """Ensure the database and its containers exist.

    With AAD auth + data-plane-only RBAC, `create_*_if_not_exists` will 403
    because creation is a control-plane operation. In that mode the DB and
    containers must be provisioned out-of-band (Bicep / `az cosmosdb sql
    container create`). We try the idempotent create path first and fall
    back to a plain `get_database_client` on auth errors.
    """
    from azure.core.exceptions import HttpResponseError

    try:
        db: DatabaseProxy = await client.create_database_if_not_exists(id=db_name)
    except HttpResponseError as exc:
        if exc.status_code in (401, 403):
            # Data-plane-only identity: assume infra is pre-provisioned.
            db = client.get_database_client(db_name)
            return db
        raise

    async def _try_create(coro):
        try:
            await coro
        except HttpResponseError as exc:
            if exc.status_code in (401, 403):
                return
            raise

    await _try_create(
        db.create_container_if_not_exists(
            id=SKILLS_CONTAINER,
            partition_key=PartitionKey(path="/skill_id"),
        )
    )
    await _try_create(
        db.create_container_if_not_exists(
            id=AUDIT_CONTAINER,
            partition_key=PartitionKey(path="/skill_id"),
        )
    )
    # usage_events gets a default TTL so M2 ingestion doesn't have to migrate.
    await _try_create(
        db.create_container_if_not_exists(
            id=USAGE_EVENTS_CONTAINER,
            partition_key=PartitionKey(path="/skill_id"),
            default_ttl=USAGE_EVENTS_TTL_SECONDS,
        )
    )
    # M1 — machine identity.
    await _try_create(
        db.create_container_if_not_exists(
            id=API_KEYS_CONTAINER,
            partition_key=PartitionKey(path="/key_id"),
        )
    )
    # M2 — system-wide ephemeral state (curator pause flag, etc.).
    await _try_create(
        db.create_container_if_not_exists(
            id=SYSTEM_STATE_CONTAINER,
            partition_key=PartitionKey(path="/key"),
        )
    )
    # M3 — Curator LLM review proposals (PK /run_id for cheap per-run listing).
    await _try_create(
        db.create_container_if_not_exists(
            id=REVIEW_PROPOSALS_CONTAINER,
            partition_key=PartitionKey(path="/run_id"),
        )
    )
    return db


def get_container(db: DatabaseProxy, name: str) -> ContainerProxy:
    return db.get_container_client(name)
