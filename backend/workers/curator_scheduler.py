"""Curator scheduler — long-running process that invokes the curator on a cadence.

In dev, run as a plain Python process. In prod, an Azure Function Timer
trigger calls `execute_pass` directly — this worker is the local-emulator
equivalent (AGENTS.md §6).

Cadence is intentionally simple: sleep for `settings.curator_schedule_cron`
parsed as an "every N seconds" override when prefixed with `@every:` (e.g.
`@every:3600`), otherwise we fall back to a daily 24h sleep loop.

We don't pull in `croniter` — true cron parsing is left to the Azure Function
in prod. Local-dev only needs a loop you can leave running.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from backend.core.blob import ensure_containers as ensure_blob_containers
from backend.core.blob import get_blob_service
from backend.core.config import get_settings
from backend.core.cosmos import (
    AUDIT_CONTAINER,
    REVIEW_PROPOSALS_CONTAINER,
    SKILLS_CONTAINER,
    SYSTEM_STATE_CONTAINER,
    ensure_containers,
    get_container,
    get_cosmos_client,
)
from backend.core.errors import CuratorPaused, LockUnavailable
from backend.core.logging import configure_logging, get_logger
from backend.core.redis import get_redis
from backend.services import curator as curator_svc
from backend.services import curator_review as curator_review_svc
from backend.services.llm import FoundryLLMProvider, LLMProvider

log = get_logger(__name__)

_DEFAULT_SLEEP_SECONDS = 24 * 3600


def _sleep_seconds_from_cron(expr: str) -> int:
    if expr.startswith("@every:"):
        try:
            return max(1, int(expr.split(":", 1)[1]))
        except ValueError:
            pass
    return _DEFAULT_SLEEP_SECONDS


async def run_forever() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    cosmos = get_cosmos_client(settings)
    db = await ensure_containers(cosmos, settings.cosmos_db_name)
    redis = get_redis(settings)
    blob = get_blob_service(settings)
    await ensure_blob_containers(blob, settings)

    skills = get_container(db, SKILLS_CONTAINER)
    audit = get_container(db, AUDIT_CONTAINER)
    system_state = get_container(db, SYSTEM_STATE_CONTAINER)
    review_proposals = get_container(db, REVIEW_PROPOSALS_CONTAINER)

    sleep_s = _sleep_seconds_from_cron(settings.curator_schedule_cron)
    log.info("curator_scheduler_started", extra={"sleep_s": sleep_s})

    review_provider: LLMProvider | None = None
    if settings.curator_review_enabled:
        try:
            review_provider = FoundryLLMProvider(settings)
            log.info(
                "curator_review_scheduler_armed",
                extra={"cron": settings.curator_review_schedule_cron},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "curator_review_scheduler_disabled",
                extra={"err": str(exc)},
            )
            review_provider = None

    stop = asyncio.Event()

    def _shutdown(*_: object) -> None:
        stop.set()

    with contextlib.suppress(NotImplementedError):
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _shutdown)

    try:
        while not stop.is_set():
            try:
                record = await curator_svc.execute_pass(
                    dry_run=False,
                    skills=skills,
                    audit=audit,
                    blob=blob,
                    redis=redis,
                    system_state=system_state,
                    settings=settings,
                    actor="system:curator-scheduler",
                )
                log.info(
                    "curator_scheduler_pass_done",
                    extra={"run_id": record.run_id, "n": len(record.transitions)},
                )
            except CuratorPaused:
                log.info("curator_scheduler_paused")
            except LockUnavailable:
                log.info("curator_scheduler_lock_busy")
            except Exception as exc:  # noqa: BLE001
                log.exception("curator_scheduler_error", extra={"err": str(exc)})

            if review_provider is not None:
                try:
                    review_rec = await curator_review_svc.execute_review_pass(
                        provider=review_provider,
                        skills=skills,
                        audit=audit,
                        review_proposals=review_proposals,
                        system_state=system_state,
                        blob=blob,
                        redis=redis,
                        settings=settings,
                        actor="system:curator-review-scheduler",
                    )
                    log.info(
                        "curator_review_scheduler_pass_done",
                        extra={
                            "run_id": review_rec.run_id,
                            "proposals": review_rec.proposals_emitted,
                            "aborted_reason": review_rec.aborted_reason,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "curator_review_scheduler_error", extra={"err": str(exc)}
                    )

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=sleep_s)
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        with contextlib.suppress(Exception):
            await blob.close()
        with contextlib.suppress(Exception):
            await cosmos.close()
        log.info("curator_scheduler_stopped")


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
