"""Defender worker — `python -m backend.workers.defender`.

BLPOPs `queue:defender`, reads the skill doc from Cosmos, materializes the
pending bundle bytes, runs the configured `DefenderScanner`, writes the
`DefenderReport` back to Cosmos on the skill doc, records an audit row,
and pushes a placeholder `defender.completed` event onto `queue:notifications`
for the M5-5 notifier to consume.

State machine:
    pending → scanning → clean | flagged | failed

* clean   — no findings; downstream admin review proceeds normally.
* flagged — `overall_severity in (low, medium, high, critical)`; admin
            review UI surfaces the report and (for medium+) requires a
            justification on approve.
* failed  — scanner exception or `DefenderTooLarge`; janitor sweep
            re-queues (`pending|failed` older than threshold). For
            `too_large` the worker also records a finding so the admin can
            see what happened without re-running.

AGENTS.md compliance:
- Cosmos write FIRST, Redis push AFTER (rule #1).
- Redis push failure is swallowed; Cosmos doc is the durable record (rule #4).
- No `delete_item` / `delete_blob` anywhere (rule §5).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import signal
from datetime import UTC, datetime

from azure.cosmos.aio import CosmosClient

from backend.core.blob import published_blob_path
from backend.core.config import get_settings
from backend.core.cosmos import (
    AUDIT_CONTAINER,
    SKILLS_CONTAINER,
    ensure_containers,
    get_container,
    get_cosmos_client,
)
from backend.core.errors import LLMProviderError
from backend.core.logging import bind, configure_logging, get_logger
from backend.core.redis import (
    key_cache_item,
    key_queue_defender,
)
from backend.core.telemetry import configure_telemetry
from backend.models.defender import (
    DefenderFinding,
    DefenderReport,
    DefenderSeverity,
    TokenUsage,
)
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc
from backend.services.defender import make_scanner
from backend.services.defender.scanner import DefenderTooLarge
from backend.services.notifier import (
    build_event,
    enqueue_notification,
    make_idempotency_key,
)

log = get_logger(__name__)


def _decode_bundle(doc: SkillDoc) -> bytes:
    """Pull the pending bundle bytes off the doc.

    M0-shaped docs always set `pending_bundle_b64` at upload (see
    `services/upload.py`). If absent (M1+ when bundle bytes move to a
    staging blob), the worker should fetch from staging — out of scope
    for M5-2, return empty bytes and let the scanner treat it as a
    trivially-clean (or, if you prefer, errored) bundle.
    """
    if not doc.pending_bundle_b64:
        return b""
    return base64.b64decode(doc.pending_bundle_b64)


async def _load_bundle_bytes(doc: SkillDoc, *, blob, settings) -> bytes:
    if doc.pending_bundle_b64:
        return _decode_bundle(doc)
    if doc.status == "approved" and doc.bundle is not None and blob is not None:
        src = blob.get_container_client(settings.blob_published_container).get_blob_client(
            published_blob_path(doc.skill_id, doc.version)
        )
        downloader = await src.download_blob()
        return await downloader.readall()
    return b""


def _too_large_report(scanner_name: str, exc: DefenderTooLarge) -> DefenderReport:
    return DefenderReport(
        overall_severity=DefenderSeverity.HIGH,
        findings=[
            DefenderFinding(
                rule="skill.too_large",
                severity="high",
                location="bundle",
                excerpt="",
                explanation=(
                    f"Bundle text ({exc.char_count} chars) exceeds defender "
                    f"input budget ({exc.char_budget} chars). The skill must "
                    f"be split into smaller skills or rejected."
                ),
            )
        ],
        model=scanner_name,
        scanned_at=datetime.now(UTC),
        scan_duration_ms=0,
        token_usage=TokenUsage(),
        notes="skill.too_large",
    )


async def process_one(
    *,
    doc_id: str,
    cosmos_client: CosmosClient,
    redis,
    settings,
    scanner=None,
    blob=None,
) -> None:
    """One BLPOP tick. Exposed for tests so they can drive a single message
    without spinning the long-running loop."""
    if scanner is None:
        scanner = make_scanner(settings.defender_provider, settings=settings)

    db = cosmos_client.get_database_client(settings.cosmos_db_name)
    skills = get_container(db, SKILLS_CONTAINER)
    audit = get_container(db, AUDIT_CONTAINER)

    # Doc id encodes skill_id (`{skill_id}:{version}:{nonce}`) — partition lookup.
    skill_id = doc_id.split(":", 1)[0]
    bind(skill_id=skill_id, actor="system:defender")

    try:
        raw = await skills.read_item(item=doc_id, partition_key=skill_id)
    except Exception as exc:
        log.warning("defender_doc_missing", extra={"doc_id": doc_id, "err": str(exc)})
        return

    doc = SkillDoc.model_validate(raw)
    # M5-3: short-circuit on quarantined. Same guarantee as the classifier
    # — a quarantined skill never re-enters scan flow even if a stale
    # message was re-pushed by the janitor before the admin acted.
    if doc.status == "quarantined":
        log.info(
            "defender_skipped_quarantined",
            extra={"doc_id": doc_id, "skill_id": skill_id},
        )
        return
    before = {
        "status": doc.status,
        "defender_status": doc.defender_status,
        "defender_severity": doc.defender_severity,
    }

    # Mark scanning. Best-effort — if this write fails we still try the scan;
    # the final replace_item is what matters for the state machine.
    doc.defender_status = "scanning"
    with contextlib.suppress(Exception):
        await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))

    report: DefenderReport
    final_status: str
    failed_reason: str | None = None
    try:
        bundle_bytes = await _load_bundle_bytes(doc, blob=blob, settings=settings)
        report = await scanner.scan(bundle_bytes=bundle_bytes)
        final_status = "clean" if report.overall_severity == DefenderSeverity.CLEAN else "flagged"
    except DefenderTooLarge as exc:
        log.warning("defender_too_large", extra={"chars": exc.char_count})
        report = _too_large_report(getattr(scanner, "name", "unknown"), exc)
        final_status = "failed"
        failed_reason = "skill.too_large"
    except LLMProviderError as exc:
        log.exception("defender_llm_failed")
        failed_reason = f"llm_provider_error: {exc}"
        doc.defender_status = "failed"
        with contextlib.suppress(Exception):
            await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
        with contextlib.suppress(Exception):
            await audit_svc.record(
                audit,
                skill_id=skill_id,
                action="classify_failed",  # reuse existing audit action for now
                actor="system:defender",
                before=before,
                metadata={"phase": "defender", "error": str(exc)},
            )
        return
    except Exception as exc:  # pragma: no cover — defensive
        log.exception("defender_unexpected_failure")
        failed_reason = f"unexpected: {exc}"
        doc.defender_status = "failed"
        with contextlib.suppress(Exception):
            await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
        return

    # Persist report + status (Cosmos-first).
    doc.defender_status = final_status  # type: ignore[assignment]
    doc.defender_severity = str(report.overall_severity)
    doc.defender_report = report.model_dump(mode="json")
    doc.defender_scanned_at = report.scanned_at
    try:
        await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
    except Exception:  # pragma: no cover
        log.exception("defender_persist_failed")
        return

    await audit_svc.record(
        audit,
        skill_id=skill_id,
        action="classify",  # M5-2: piggy-back on existing audit action; M5-6 will add a dedicated `defender.*` action.
        actor="system:defender",
        before=before,
        after={
            "defender_status": final_status,
            "defender_severity": str(report.overall_severity),
            "findings_count": len(report.findings),
        },
        metadata={
            "phase": "defender",
            "model": report.model,
            "scan_duration_ms": report.scan_duration_ms,
            "input_tokens": report.token_usage.input_tokens,
            "output_tokens": report.token_usage.output_tokens,
            "failed_reason": failed_reason,
        },
    )

    # Bust the item cache so the admin UI sees the new defender_status.
    with contextlib.suppress(Exception):
        await redis.delete(key_cache_item(skill_id))

    # M5-6: emit a real notifier event. The two interesting outcomes from
    # the admin's perspective are:
    #   * defender_status == 'clean'   → skill is now awaiting normal review
    #   * defender_status == 'flagged' → admins need to see the report and
    #                                    either override or quarantine
    # `failed` is operational noise; the janitor sweep re-queues it and the
    # admin sees `defender_status=failed` in the UI without needing email.
    if final_status == "clean":
        await enqueue_notification(
            build_event(
                "skill.awaiting_review",
                skill_id=skill_id,
                payload={
                    "skill_id": skill_id,
                    "version": doc.version,
                    "name": doc.name,
                    "defender_severity": str(report.overall_severity),
                    "uploader": doc.uploader,
                },
                idempotency_key=make_idempotency_key(
                    "skill.awaiting_review",
                    skill_id=skill_id,
                    version=doc.version,
                    extra=doc.id,
                ),
            ),
            redis=redis,
        )
    elif final_status == "flagged":
        await enqueue_notification(
            build_event(
                "defender.flagged",
                skill_id=skill_id,
                payload={
                    "skill_id": skill_id,
                    "version": doc.version,
                    "name": doc.name,
                    "defender_severity": str(report.overall_severity),
                    "findings_count": len(report.findings),
                    "uploader": doc.uploader,
                },
                idempotency_key=make_idempotency_key(
                    "defender.flagged",
                    skill_id=skill_id,
                    version=doc.version,
                    extra=doc.id,
                ),
            ),
            redis=redis,
        )

    log.info(
        "defender_ok",
        extra={
            "defender_status": final_status,
            "severity": str(report.overall_severity),
            "findings": len(report.findings),
        },
    )


async def run_loop(stop: asyncio.Event | None = None) -> None:
    """Long-running BLPOP loop. `stop` lets tests trigger graceful shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    import os

    os.environ.setdefault("OTEL_SERVICE_ROLE", "worker")
    configure_telemetry(settings)

    from backend.core.redis import get_redis

    cosmos_client = get_cosmos_client(settings)
    redis = get_redis(settings)
    from backend.core.blob import get_blob_service

    blob = get_blob_service(settings)
    await ensure_containers(cosmos_client, settings.cosmos_db_name)

    stop = stop or asyncio.Event()
    scanner = make_scanner(settings.defender_provider, settings=settings)
    log.info("defender_worker_started", extra={"provider": settings.defender_provider})

    try:
        while not stop.is_set():
            try:
                msg = await redis.blpop(
                    [key_queue_defender()],
                    timeout=settings.defender_blpop_timeout_seconds,
                )
            except Exception as exc:
                log.warning("blpop_failed", extra={"err": str(exc)})
                await asyncio.sleep(1.0)
                continue
            if not msg:
                continue
            _key, doc_id = msg
            await process_one(
                doc_id=doc_id,
                cosmos_client=cosmos_client,
                redis=redis,
                settings=settings,
                scanner=scanner,
                blob=blob,
            )
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        with contextlib.suppress(Exception):
            await blob.close()
        with contextlib.suppress(Exception):
            await cosmos_client.close()
        log.info("defender_worker_stopped")


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
