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
    API_KEYS_CONTAINER,
    AUDIT_CONTAINER,
    REVIEW_PROPOSALS_CONTAINER,
    SKILLS_CONTAINER,
    SYSTEM_STATE_CONTAINER,
    USAGE_EVENTS_CONTAINER,
    get_container,
)
from backend.services.llm import FakeLLMProvider, FoundryLLMProvider, LLMProvider


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


def get_api_keys_container(db: DatabaseProxy = Depends(get_db)) -> ContainerProxy:
    return get_container(db, API_KEYS_CONTAINER)


def get_system_state_container(db: DatabaseProxy = Depends(get_db)) -> ContainerProxy:
    return get_container(db, SYSTEM_STATE_CONTAINER)


def get_review_proposals_container(
    db: DatabaseProxy = Depends(get_db),
) -> ContainerProxy:
    return get_container(db, REVIEW_PROPOSALS_CONTAINER)


def settings_dep() -> Settings:
    return get_settings()


# ---- LLM provider DI (M3) -----------------------------------------------

_llm_provider_instance: LLMProvider | None = None


def _build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.curator_review_provider == "fake":
        # Tests override via ``app.dependency_overrides[get_llm_provider]``;
        # this default is empty-canned and will raise on first call (which
        # surfaces misconfiguration loudly).
        return FakeLLMProvider(canned=[])
    return FoundryLLMProvider(settings)


def get_llm_provider(settings: Settings = Depends(settings_dep)) -> LLMProvider:
    global _llm_provider_instance
    if _llm_provider_instance is None:
        _llm_provider_instance = _build_llm_provider(settings)
    return _llm_provider_instance


def reset_llm_provider_singleton() -> None:
    """Test helper — drops the cached provider so the next call rebuilds it."""
    global _llm_provider_instance
    _llm_provider_instance = None
