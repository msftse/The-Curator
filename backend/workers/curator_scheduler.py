"""Curator scheduler — long-running loop OR single-shot pass (M4).

Two invocation modes:

- **Long-running loop** (default, used by `make curator` in local dev and by
  the legacy App Service worker). Sleeps `curator_schedule_cron` between
  passes, never exits cleanly without a signal. Same behaviour as M2.

- **Single-shot** (`--once`, used by the K8s CronJob — see
  `charts/agentic-skill-hub/templates/curator/cronjob.yaml`). Runs exactly
  one deterministic curator pass (and one LLM review pass if
  `curator_review_enabled=True`), then exits `0`. Add `--dry-run` to skip
  mutations on either pass.

Cadence is intentionally simple: sleep for `settings.curator_schedule_cron`
parsed as an "every N seconds" override when prefixed with `@every:` (e.g.
`@every:3600`), otherwise we fall back to a daily 24h sleep loop. K8s
CronJob ownership replaces this loop in cluster (M4).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from datetime import UTC, datetime

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
from backend.services.notifier import (
    build_event,
    enqueue_notification,
    make_idempotency_key,
)

log = get_logger(__name__)

_DEFAULT_SLEEP_SECONDS = 24 * 3600


def _sleep_seconds_from_cron(expr: str) -> int:
    if expr.startswith("@every:"):
        try:
            return max(1, int(expr.split(":", 1)[1]))
        except ValueError:
            pass
    return _DEFAULT_SLEEP_SECONDS


async def _run_one_pass(
    *,
    skills,
    audit,
    blob,
    redis,
    system_state,
    review_proposals,
    settings,
    review_provider: LLMProvider | None,
    dry_run: bool,
    actor: str,
) -> int:
    """Run the deterministic + (optional) review pass once. Returns exit code."""
    exit_code = 0
    pass_started = datetime.now(UTC)
    digest_payload: dict[str, object] = {
        "dry_run": dry_run,
        "actor": actor,
        "window_start": pass_started.isoformat(),
        "window_end": pass_started.isoformat(),
        "pass_count": 1,
        "transitions_total": 0,
        "transitions_applied": 0,
        "transition_count": 0,
        "stale_count": 0,
        "archived_count": 0,
        "snapshot_count": 0,
        "error_count": 0,
        "dry_run_diffs": 0,
        "report_url": "",
        "snapshot_name": None,
        "skipped_pinned": 0,
        "deterministic_error": None,
        "review_error": None,
        "review_proposals": 0,
        "review_aborted_reason": None,
        "run_id": None,
    }
    try:
        record = await curator_svc.execute_pass(
            dry_run=dry_run,
            skills=skills,
            audit=audit,
            blob=blob,
            redis=redis,
            system_state=system_state,
            settings=settings,
            actor=actor,
        )
        digest_payload["run_id"] = record.run_id
        digest_payload["transitions_total"] = len(record.transitions)
        digest_payload["transitions_applied"] = sum(
            1 for t in record.transitions if getattr(t, "applied", False)
        )
        digest_payload["transition_count"] = digest_payload["transitions_applied"]
        digest_payload["stale_count"] = sum(
            1 for t in record.transitions if t.after == "stale" and getattr(t, "applied", False)
        )
        digest_payload["archived_count"] = sum(
            1 for t in record.transitions if t.after == "archived" and getattr(t, "applied", False)
        )
        digest_payload["snapshot_count"] = 1 if record.snapshot_name else 0
        digest_payload["dry_run_diffs"] = len(record.transitions) if dry_run else 0
        digest_payload["snapshot_name"] = record.snapshot_name
        digest_payload["skipped_pinned"] = len(record.skipped_pinned)
        digest_payload["report_url"] = (
            f"{settings.notifier_review_url_base.rstrip('/')}/admin/curator/runs/{record.run_id}"
            if settings.notifier_review_url_base
            else ""
        )
        log.info(
            "curator_scheduler_pass_done",
            extra={
                "run_id": record.run_id,
                "n": len(record.transitions),
                "dry_run": dry_run,
            },
        )
    except CuratorPaused:
        log.info("curator_scheduler_paused")
        digest_payload["deterministic_error"] = "paused"
        digest_payload["error_count"] = int(digest_payload["error_count"]) + 1
    except LockUnavailable:
        log.info("curator_scheduler_lock_busy")
        digest_payload["deterministic_error"] = "lock_busy"
        # Lock contention is not a hard failure — another pass holds it. Exit
        # 0 so K8s does not retry-storm.
    except Exception as exc:  # noqa: BLE001
        log.exception("curator_scheduler_error", extra={"err": str(exc)})
        digest_payload["deterministic_error"] = str(exc)
        digest_payload["error_count"] = int(digest_payload["error_count"]) + 1
        exit_code = 1

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
                actor=actor,
            )
            digest_payload["review_proposals"] = review_rec.proposals_emitted
            digest_payload["review_aborted_reason"] = review_rec.aborted_reason
            log.info(
                "curator_review_scheduler_pass_done",
                extra={
                    "run_id": review_rec.run_id,
                    "proposals": review_rec.proposals_emitted,
                    "aborted_reason": review_rec.aborted_reason,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("curator_review_scheduler_error", extra={"err": str(exc)})
            digest_payload["review_error"] = str(exc)
            digest_payload["error_count"] = int(digest_payload["error_count"]) + 1
            exit_code = 1

    digest_payload["window_end"] = datetime.now(UTC).isoformat()

    # M5-6: fire a per-pass curator report event. The historical event name is
    # `curator.weekly_report` because the default schedule is weekly, but the
    # payload window is the actual pass duration so ad-hoc or more frequent
    # schedules do not pretend to be weekly rollups.
    run_id = digest_payload["run_id"] or f"adhoc-{actor}-{pass_started.isoformat()}"
    await enqueue_notification(
        build_event(
            "curator.weekly_report",
            skill_id=None,
            payload=digest_payload,
            idempotency_key=make_idempotency_key(
                "curator.weekly_report",
                skill_id=None,
                extra=str(run_id),
            ),
        ),
        redis=redis,
    )

    return exit_code


async def run_forever(*, once: bool = False, dry_run: bool = False) -> int:
    """Top-level entrypoint.

    When `once=True`, performs a single pass and returns its exit code.
    Otherwise loops until SIGINT/SIGTERM (always returns 0 on graceful stop).
    """
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
    log.info(
        "curator_scheduler_started",
        extra={"sleep_s": sleep_s, "once": once, "dry_run": dry_run},
    )

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

    actor = "system:curator-cronjob" if once else "system:curator-scheduler"

    exit_code = 0

    try:
        if once:
            exit_code = await _run_one_pass(
                skills=skills,
                audit=audit,
                blob=blob,
                redis=redis,
                system_state=system_state,
                review_proposals=review_proposals,
                settings=settings,
                review_provider=review_provider,
                dry_run=dry_run,
                actor=actor,
            )
            return exit_code

        stop = asyncio.Event()

        def _shutdown(*_: object) -> None:
            stop.set()

        with contextlib.suppress(NotImplementedError):
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _shutdown)

        while not stop.is_set():
            await _run_one_pass(
                skills=skills,
                audit=audit,
                blob=blob,
                redis=redis,
                system_state=system_state,
                review_proposals=review_proposals,
                settings=settings,
                review_provider=review_provider,
                dry_run=dry_run,
                actor=actor,
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=sleep_s)
        return 0
    finally:
        with contextlib.suppress(Exception):
            await redis.aclose()
        with contextlib.suppress(Exception):
            await blob.close()
        with contextlib.suppress(Exception):
            await cosmos.close()
        log.info("curator_scheduler_stopped", extra={"exit_code": exit_code})


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m backend.workers.curator_scheduler",
        description=(
            "Curator scheduler. Default: long-running loop. "
            "--once: single-shot pass for K8s CronJob invocation."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one deterministic + review pass and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Take a snapshot but apply no mutations on either pass.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(run_forever(once=args.once, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
