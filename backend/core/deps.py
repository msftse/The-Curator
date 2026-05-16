"""FastAPI dependency injection — pull pre-initialized clients off `app.state`.

Clients are constructed in `backend.app.lifespan` and attached to app state.
Handlers and services receive them via `Depends` — never instantiate inside
business logic (AGENTS.md §8).
"""

from __future__ import annotations

from azure.cosmos.aio import ContainerProxy, DatabaseProxy
from azure.storage.blob.aio import BlobServiceClient
from fastapi import Depends, Request
from redis.asyncio import Redis

from backend.core.config import Settings, get_settings
from backend.core.cosmos import (
    AUDIT_CONTAINER,
    SKILLS_CONTAINER,
    USAGE_EVENTS_CONTAINER,
    get_container,
)


def get_db(request: Request) -> DatabaseProxy:
    return request.app.state.cosmos_db


def get_redis_client(request: Request) -> Redis:
    return request.app.state.redis


def get_blob(request: Request) -> BlobServiceClient:
    return request.app.state.blob


def get_skills_container(db: DatabaseProxy = Depends(get_db)) -> ContainerProxy:
    return get_container(db, SKILLS_CONTAINER)


def get_audit_container(db: DatabaseProxy = Depends(get_db)) -> ContainerProxy:
    return get_container(db, AUDIT_CONTAINER)


def get_usage_container(db: DatabaseProxy = Depends(get_db)) -> ContainerProxy:
    return get_container(db, USAGE_EVENTS_CONTAINER)


def settings_dep() -> Settings:
    return get_settings()
