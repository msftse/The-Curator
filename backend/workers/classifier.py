"""Classifier worker — `python -m backend.workers.classifier`.

Pops doc ids off `queue:classifier`, runs the configured `ClassifierProvider`,
writes the result back to Cosmos, records an audit row, busts the item cache.

On exception we mark `classifier_status=failed` in Cosmos and continue the
loop. The job is dropped (M2 janitor will re-queue).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from datetime import UTC, datetime

from azure.cosmos.aio import CosmosClient

from backend.core.config import get_settings
from backend.core.cosmos import (
    AUDIT_CONTAINER,
    SKILLS_CONTAINER,
    ensure_containers,
    get_container,
    get_cosmos_client,
)
from backend.core.logging import bind, configure_logging, get_logger
from backend.core.redis import key_cache_item, key_queue_classifier
from backend.core.telemetry import configure_telemetry
from backend.models.skill import SkillDoc
from backend.services import audit as audit_svc
from backend.services.classifier_stub import make_classifier

log = get_logger(__name__)


async def process_one(
    *,
    doc_id: str,
    cosmos_client: CosmosClient,
    redis,
    settings,
) -> None:
    classifier = make_classifier(settings.classifier_provider, settings=settings)
    db = cosmos_client.get_database_client(settings.cosmos_db_name)
    skills = get_container(db, SKILLS_CONTAINER)
    audit = get_container(db, AUDIT_CONTAINER)

    # Doc id encodes skill_id (`{skill_id}:{version}:{nonce}`) — partition lookup.
    skill_id = doc_id.split(":", 1)[0]
    bind(skill_id=skill_id, actor="system:classifier")

    try:
        raw = await skills.read_item(item=doc_id, partition_key=skill_id)
    except Exception as exc:
        log.warning("classifier_doc_missing", extra={"doc_id": doc_id, "err": str(exc)})
        return

    doc = SkillDoc.model_validate(raw)
    before = {"status": doc.status, "classifier_status": doc.classifier_status}
    try:
        result = await classifier.classify(doc.skill_md_text)
        result.classified_at = datetime.now(UTC)
        doc.classification = result
        doc.classifier_status = "done"
        if doc.status == "pending":
            doc.status = "classified"
        await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
        await audit_svc.record(
            audit,
            skill_id=skill_id,
            action="classify",
            actor="system:classifier",
            before=before,
            after={
                "status": doc.status,
                "classifier_status": "done",
                "classification": result.model_dump(mode="json"),
            },
        )
        log.info("classify_ok", extra={"category": result.category})
    except Exception as exc:
        log.exception("classify_failed")
        doc.classifier_status = "failed"
        try:
            await skills.replace_item(item=doc.id, body=doc.model_dump(mode="json"))
        except Exception:  # pragma: no cover
            pass
        with contextlib.suppress(Exception):
            await audit_svc.record(
                audit,
                skill_id=skill_id,
                action="classify_failed",
                actor="system:classifier",
                metadata={"error": str(exc)},
            )
    finally:
        with contextlib.suppress(Exception):
            await redis.delete(key_cache_item(skill_id))


async def run_loop(stop: asyncio.Event | None = None) -> None:
    """Long-running BLPOP loop. `stop` lets tests trigger graceful shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    # Worker telemetry — never instruments FastAPI (it's not a web app).
    import os

    os.environ.setdefault("OTEL_SERVICE_ROLE", "worker")
    configure_telemetry(settings)

    from backend.core.redis import get_redis

    cosmos_client = get_cosmos_client(settings)
    redis = get_redis(settings)
    await ensure_containers(cosmos_client, settings.cosmos_db_name)

    stop = stop or asyncio.Event()
    log.info("classifier_worker_started")

    try:
        while not stop.is_set():
            try:
                msg = await redis.blpop(
                    [key_queue_classifier()],
                    timeout=settings.classifier_blpop_timeout_seconds,
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
            )
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        with contextlib.suppress(Exception):
            await cosmos_client.close()
        log.info("classifier_worker_stopped")


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
