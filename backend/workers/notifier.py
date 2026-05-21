"""Notifier worker — `python -m backend.workers.notifier`.

BLPOPs `queue:notifications`, deserializes a `NotificationEvent`, dedupes
by `idempotency_key` via Redis `SETNX notif:sent:{key} 1 EX 86400`,
resolves recipients (admins via Microsoft Graph for admin-audience
events, the uploader's email directly for contributor-audience events),
renders the appropriate template, and sends via Azure Communication
Services.

Design choices (vs the M5-2 placeholder push the defender worker emits):

* The notifier is **purely a consumer**. It does NOT update any Cosmos
  state on the skill. The audit row it writes records what was sent, to
  whom, and how — useful for "did we email anyone about this skill?"
  forensics. Producers (M5-6) own the Cosmos-side state changes that
  triggered the event.
* Recipient resolution for admin-audience events is cached in Redis for
  15 minutes (`admin:recipients`). Stale by design — group-membership
  changes propagate within a quarter hour, no faster, and that's fine.
* Send failures are logged but do NOT delete the dedupe lock — replay
  attempts (e.g. the producer pushing again on retry) would then fan out
  emails. We accept "one email loss on transient ACS failure" as the
  better trade vs "potentially many duplicates". Operators can re-emit
  manually if necessary.

AGENTS.md compliance:
- Cosmos audit row is the durable record (rule #1: write before Redis
  side effects matter).
- Redis is the queue + dedupe TTL + recipient cache. All keys carry a
  TTL (rule #3). The classifier-queue carve-out (rule #4) extends to
  `queue:notifications` for the same reasons: producers persist the
  motivating state in Cosmos before queueing, and a janitor sweep can
  re-emit lost events.
- No `delete_item` or `delete_blob` anywhere (rule §5).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal

from azure.cosmos.aio import CosmosClient

from backend.core.config import get_settings
from backend.core.cosmos import (
    AUDIT_CONTAINER,
    ensure_containers,
    get_container,
    get_cosmos_client,
)
from backend.core.logging import bind, configure_logging, get_logger
from backend.core.redis import (
    key_admin_recipients,
    key_notif_sent,
    key_queue_notifications,
)
from backend.core.telemetry import configure_telemetry
from backend.models.notifications import NotificationEvent
from backend.services import audit as audit_svc
from backend.services.notifier import (
    AcsEmailClient,
    AcsEmailMessage,
    GraphClient,
    make_acs_client,
    make_graph_client,
    render_template,
)

log = get_logger(__name__)


# ----- Recipient resolution -------------------------------------------


async def _resolve_recipients(
    event: NotificationEvent,
    *,
    graph: GraphClient,
    redis,
    settings,
) -> list[str]:
    """Pick the audience for `event`.

    Contributor events go to the uploader (must be on the event).
    Admin events go to the Entra group resolved via Graph, cached for
    `notifier_recipients_cache_ttl_seconds` in Redis.
    """
    if event.is_contributor_event():
        if not event.contributor_email:
            log.warning(
                "notifier.contributor_event_missing_email",
                extra={"event_id": event.event_id, "event_type": event.event_type},
            )
            return []
        return [event.contributor_email.lower()]

    # Admin audience.
    cache_key = key_admin_recipients()
    cached: str | None = None
    with contextlib.suppress(Exception):
        cached = await redis.get(cache_key)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            # Bad cache entry — drop and re-resolve.
            pass

    recipients = await graph.list_admin_recipients()
    if recipients:
        # Best-effort cache write. AGENTS.md §4 rule #2: a cache miss is
        # normal; we never fail the send because the cache write failed.
        with contextlib.suppress(Exception):
            await redis.set(
                cache_key,
                json.dumps(recipients),
                ex=settings.notifier_recipients_cache_ttl_seconds,
            )
    return recipients


# ----- Dedupe ---------------------------------------------------------


async def _claim_idempotency(event: NotificationEvent, *, redis, settings) -> bool:
    """Try to claim the event for sending.

    Returns True if this is the first time we've seen the
    `idempotency_key`, False if a previous worker (or a previous loop
    iteration in the same worker) already claimed it. Returns True on
    Redis-down (fail open — we'd rather double-send than swallow
    notifications silently).
    """
    key = key_notif_sent(event.ensure_idempotency_key())
    try:
        acquired = await redis.set(key, "1", nx=True, ex=settings.notifier_dedupe_ttl_seconds)
    except Exception as exc:
        log.warning(
            "notifier.dedupe_redis_failed_fail_open",
            extra={"event_id": event.event_id, "err": str(exc)},
        )
        return True
    return bool(acquired)


# ----- Main per-message handler ---------------------------------------


async def process_one(
    *,
    raw: str,
    cosmos_client: CosmosClient,
    redis,
    settings,
    acs: AcsEmailClient,
    graph: GraphClient,
) -> None:
    """Handle one queued notification message.

    Exposed for tests so they can drive a single tick without spinning
    the long-running BLPOP loop.
    """
    try:
        event = NotificationEvent.model_validate_json(raw)
    except Exception as exc:
        log.warning("notifier.unparseable_event", extra={"raw_prefix": raw[:200], "err": str(exc)})
        return

    bind(skill_id=event.skill_id or "-", actor="system:notifier")

    if not await _claim_idempotency(event, redis=redis, settings=settings):
        log.info(
            "notifier.deduped",
            extra={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "idempotency_key": event.idempotency_key,
            },
        )
        return

    recipients = await _resolve_recipients(event, graph=graph, redis=redis, settings=settings)
    if not recipients:
        log.warning(
            "notifier.no_recipients",
            extra={"event_id": event.event_id, "event_type": event.event_type},
        )
        return

    try:
        rendered = render_template(event.event_type, event.payload)
    except KeyError as exc:
        log.warning(
            "notifier.unknown_event_type",
            extra={"event_id": event.event_id, "err": str(exc)},
        )
        return

    message = AcsEmailMessage(
        sender=settings.acs_sender_address,
        recipients=recipients,
        subject=rendered.subject,
        plain_text=rendered.plain_text,
        html=rendered.html,
        correlation_id=event.event_id,
    )

    provider_message_id: str | None = None
    send_error: str | None = None
    try:
        provider_message_id = await acs.send(message)
    except Exception as exc:
        send_error = str(exc)
        log.exception("notifier.send_failed")

    # Audit row — what we attempted, to whom, and the outcome. Skill-
    # scoped audits use the event's skill_id; non-skill events (e.g.
    # curator.weekly_report) use a synthetic id so the partition key
    # stays populated.
    db = cosmos_client.get_database_client(settings.cosmos_db_name)
    audit = get_container(db, AUDIT_CONTAINER)
    with contextlib.suppress(Exception):
        await audit_svc.record(
            audit,
            skill_id=event.skill_id or f"_notifier:{event.event_type}",
            # Reuse an existing audit action so we don't have to extend
            # the AuditAction literal mid-milestone. M5-6 / M5-8 will
            # introduce a dedicated `notify` action and wire it in.
            action="classify",
            actor="system:notifier",
            metadata={
                "phase": "notifier",
                "event_id": event.event_id,
                "event_type": event.event_type,
                "idempotency_key": event.idempotency_key,
                "recipients": recipients,
                "provider": acs.name,
                "provider_message_id": provider_message_id,
                "error": send_error,
            },
        )

    log.info(
        "notifier.sent" if send_error is None else "notifier.send_attempted",
        extra={
            "event_id": event.event_id,
            "event_type": event.event_type,
            "recipients": len(recipients),
            "provider_message_id": provider_message_id,
            "error": send_error,
        },
    )


# ----- Long-running loop ----------------------------------------------


async def run_loop(stop: asyncio.Event | None = None) -> None:
    """Long-running BLPOP loop. `stop` lets tests trigger graceful shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    import os

    os.environ.setdefault("OTEL_SERVICE_ROLE", "notifier")
    configure_telemetry(settings)

    from backend.core.redis import get_redis

    cosmos_client = get_cosmos_client(settings)
    redis = get_redis(settings)
    await ensure_containers(cosmos_client, settings.cosmos_db_name)

    acs = make_acs_client(settings.notifier_provider, settings=settings)
    graph = make_graph_client(settings.notifier_graph_provider, settings=settings)

    stop = stop or asyncio.Event()
    log.info(
        "notifier_worker_started",
        extra={
            "acs_provider": settings.notifier_provider,
            "graph_provider": settings.notifier_graph_provider,
        },
    )

    try:
        while not stop.is_set():
            try:
                msg = await redis.blpop(
                    [key_queue_notifications()],
                    timeout=settings.notifier_blpop_timeout_seconds,
                )
            except Exception as exc:
                log.warning("blpop_failed", extra={"err": str(exc)})
                await asyncio.sleep(1.0)
                continue
            if not msg:
                continue
            _key, raw = msg
            await process_one(
                raw=raw,
                cosmos_client=cosmos_client,
                redis=redis,
                settings=settings,
                acs=acs,
                graph=graph,
            )
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        with contextlib.suppress(Exception):
            await cosmos_client.close()
        log.info("notifier_worker_stopped")


def main() -> None:
    stop = asyncio.Event()

    def _handle_signal(*_a) -> None:
        stop.set()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle_signal)

    try:
        loop.run_until_complete(run_loop(stop))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
