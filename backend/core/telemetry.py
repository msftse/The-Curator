"""Application Insights / OpenTelemetry wiring (M1).

Behavior contract:
- `configure_telemetry(settings)` with an empty connection string is a no-op.
  Local dev (`docker compose up`) stays silent — zero OTel chatter, zero
  Azure spend (AGENTS.md §6).
- With a non-empty connection string, configures the Azure Monitor exporter
  and instruments FastAPI (when an app is passed), HTTPX, and redis-py.
- Idempotent — safe to call from both `backend.app.lifespan` and
  `backend.workers.classifier.main()`.

The classifier worker is not a FastAPI app, so `instrument_app(app)` is
optional. The worker still wants HTTPX + Redis spans.
"""

from __future__ import annotations

from typing import Any

from backend.core.config import Settings
from backend.core.logging import get_logger

log = get_logger(__name__)

_configured = False


def configure_telemetry(settings: Settings, *, app: Any | None = None) -> None:
    """Initialize Azure Monitor + OTel instrumentation. No-op when disabled."""
    global _configured
    if _configured:
        if app is not None:
            _instrument_app(app)
        return
    conn = settings.appinsights_connection_string
    if not conn:
        log.debug("telemetry_disabled")
        _configured = True
        return

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
    except ImportError as exc:
        log.warning("telemetry_imports_unavailable", extra={"err": str(exc)})
        _configured = True
        return

    import os

    os.environ.setdefault("OTEL_SERVICE_NAME", f"skillhub-{settings.otel_service_role}")
    configure_azure_monitor(connection_string=conn)
    try:
        HTTPXClientInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        log.warning("httpx_instrument_failed", extra={"err": str(exc)})
    try:
        RedisInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        log.warning("redis_instrument_failed", extra={"err": str(exc)})
    if app is not None:
        _instrument_app(app)
    _configured = True
    log.info("telemetry_configured", extra={"role": settings.otel_service_role})


def _instrument_app(app: Any) -> None:
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception as exc:  # pragma: no cover
        log.warning("fastapi_instrument_failed", extra={"err": str(exc)})


def reset_for_tests() -> None:
    """Test-only helper to undo the idempotent guard."""
    global _configured
    _configured = False
