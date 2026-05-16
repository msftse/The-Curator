"""FastAPI app factory.

Boot order (lifespan):
1. Configure JSON logging.
2. Build async Cosmos/Redis/Blob clients.
3. `ensure_containers()` for Cosmos and Blob (idempotent).
4. Attach clients to `app.state`.
5. Tear them down on shutdown.

Run with: `uvicorn backend.app:create_app --factory --reload`
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import admin as admin_router
from backend.api import api_keys as api_keys_router
from backend.api import curator as curator_router
from backend.api import skills as skills_router
from backend.api import uploads as uploads_router
from backend.core import blob as blob_core
from backend.core import cosmos as cosmos_core
from backend.core.auth import select_provider
from backend.core.config import get_settings
from backend.core.cosmos import API_KEYS_CONTAINER, get_container
from backend.core.errors import register_exception_handlers
from backend.core.logging import configure_logging, get_logger
from backend.core.redis import get_redis
from backend.core.telemetry import configure_telemetry
from backend.models.api import HealthResponse

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    # Telemetry is a no-op when APPLICATIONINSIGHTS_CONNECTION_STRING is unset.
    configure_telemetry(settings, app=app)

    cosmos_client = cosmos_core.get_cosmos_client(settings)
    db = await cosmos_core.ensure_containers(cosmos_client, settings.cosmos_db_name)

    redis = get_redis(settings)
    # Touch redis so failures surface during boot in dev (non-fatal in tests).
    with contextlib.suppress(Exception):
        await redis.ping()

    blob = blob_core.get_blob_service(settings)
    await blob_core.ensure_containers(blob, settings)

    app.state.settings = settings
    app.state.cosmos_client = cosmos_client
    app.state.cosmos_db = db
    app.state.redis = redis
    app.state.blob = blob
    app.state.api_keys_container = get_container(db, API_KEYS_CONTAINER)
    app.state.identity_provider = select_provider(settings)

    log.info("app_started", extra={"auth_mode": settings.auth_mode})
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        with contextlib.suppress(Exception):
            await blob.close()
        with contextlib.suppress(Exception):
            await cosmos_client.close()
        log.info("app_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Agentic Skill Hub",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    app.include_router(uploads_router.router)
    app.include_router(admin_router.router)
    app.include_router(api_keys_router.router)
    app.include_router(curator_router.router)
    app.include_router(skills_router.router)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        cosmos_ok = "ok"
        redis_ok = "ok"
        blob_ok = "ok"
        details: dict = {}
        try:
            await app.state.redis.ping()
        except Exception as exc:
            redis_ok = "down"
            details["redis_err"] = str(exc)
        try:
            db = app.state.cosmos_db
            await db.read()
        except Exception as exc:
            cosmos_ok = "down"
            details["cosmos_err"] = str(exc)
        try:
            await app.state.blob.get_service_properties()
        except Exception as exc:
            blob_ok = "down"
            details["blob_err"] = str(exc)
        return HealthResponse(
            ok=(cosmos_ok == "ok" and blob_ok == "ok"),
            cosmos=cosmos_ok,
            redis=redis_ok,
            blob=blob_ok,
            details=details,
        )

    return app
