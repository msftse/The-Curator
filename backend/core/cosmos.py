"""Async Cosmos DB client + idempotent container bootstrap.

Cosmos is the system of record (AGENTS.md §3). All durable writes hit Cosmos
first. Containers are created on app startup so the worker and tests can
assume they exist.
"""

from __future__ import annotations

import urllib3
from azure.cosmos import PartitionKey
from azure.cosmos.aio import ContainerProxy, CosmosClient, DatabaseProxy

from backend.core.config import Settings

# Cosmos containers per PRD §10.
SKILLS_CONTAINER = "skills"
AUDIT_CONTAINER = "audit"
USAGE_EVENTS_CONTAINER = "usage_events"
API_KEYS_CONTAINER = "api_keys"
USAGE_EVENTS_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days


def get_cosmos_client(settings: Settings) -> CosmosClient:
    """Build an async Cosmos client.

    With the local emulator + COSMOS_VERIFY_TLS=false we have to silence the
    InsecureRequestWarning the SDK emits on every call. This is a dev-only
    shortcut documented in AGENTS.md and the env example.
    """
    connection_verify = settings.cosmos_verify_tls
    if not connection_verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return CosmosClient(
        url=settings.cosmos_endpoint,
        credential=settings.cosmos_key,
        connection_verify=connection_verify,
    )


async def ensure_containers(client: CosmosClient, db_name: str) -> DatabaseProxy:
    """Create the database and its three containers if they don't exist.

    Safe to call on every app start.
    """
    db: DatabaseProxy = await client.create_database_if_not_exists(id=db_name)
    await db.create_container_if_not_exists(
        id=SKILLS_CONTAINER,
        partition_key=PartitionKey(path="/skill_id"),
    )
    await db.create_container_if_not_exists(
        id=AUDIT_CONTAINER,
        partition_key=PartitionKey(path="/skill_id"),
    )
    # usage_events gets a default TTL so M2 ingestion doesn't have to migrate.
    await db.create_container_if_not_exists(
        id=USAGE_EVENTS_CONTAINER,
        partition_key=PartitionKey(path="/skill_id"),
        default_ttl=USAGE_EVENTS_TTL_SECONDS,
    )
    # M1 — machine identity.
    await db.create_container_if_not_exists(
        id=API_KEYS_CONTAINER,
        partition_key=PartitionKey(path="/key_id"),
    )
    return db


def get_container(db: DatabaseProxy, name: str) -> ContainerProxy:
    return db.get_container_client(name)
