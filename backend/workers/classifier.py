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
from backend.core.redis import key_cache_item, key_queue_classifier, key_queue_defender
from backend.core.telemetry import configure_telemetry
from backend.models.skill import Classification, SkillDoc
from backend.services import audit as audit_svc
from backend.services.classifier_stub import make_classifier

log = get_logger(__name__)


def _merge_user_hints(
    result: Classification,
    *,
    user_category: str | None,
    user_tags: list[str],
) -> Classification:
    """Apply contributor-supplied hints to a fresh classifier result.

    - `user_category` (when non-empty) overrides `result.category` unconditionally.
      The classifier's pick is preserved on the doc audit row before this merge.
    - `user_tags` are prepended to `result.tags`, then deduped case-insensitively
      (preserving first-seen casing), then capped at 8.
    """
    merged_category = result.category
    if user_category and user_category.strip():
        merged_category = user_category.strip()

    seen: set[str] = set()
    merged_tags: list[str] = []
    for tag in list(user_tags) + list(result.tags):
        if not isinstance(tag, str):
            continue
        normalized = tag.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged_tags.append(normalized)
        if len(merged_tags) >= 8:
            break

    return result.model_copy(update={"category": merged_category, "tags": merged_tags})


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
    # M5-3: a quarantined skill must never re-enter classifier flow even
    # if a stale queue message resurrects it. Bail before touching Cosmos.
    if doc.status == "quarantined":
        log.info(
            "classifier_skipped_quarantined",
            extra={"doc_id": doc_id, "skill_id": skill_id},
        )
        return
    before = {"status": doc.status, "classifier_status": doc.classifier_status}
    try:
        result = await classifier.classify(doc.skill_md_text)
        result.classified_at = datetime.now(UTC)
        # Merge contributor-supplied hints with classifier output.
        # Policy: user_category wins outright; tags = union(user_tags,
        # classifier_tags) with user order first, case-insensitive dedup,
        # capped at 8. See AGENTS.md / docs/PRD.md §7.2.
        result = _merge_user_hints(result, user_category=doc.user_category, user_tags=doc.user_tags)
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
        # M5-2: hand the doc off to the defender scan queue. Cosmos write
        # above is the source of truth; if this RPUSH fails the janitor
        # sweep will re-queue based on `defender_status=pending` age
        # (AGENTS.md §4 rule 4 mitigation, same shape as the upload path).
        if doc.status in {"pending", "classified"} and doc.defender_status in {
            "pending",
            "failed",
        }:
            try:
                await redis.rpush(key_queue_defender(), doc.id)
            except Exception as exc:  # pragma: no cover — defensive
                log.warning("defender_enqueue_failed", extra={"err": str(exc)})
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
