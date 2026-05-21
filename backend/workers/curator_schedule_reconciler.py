"""Curator schedule reconciler (M5-7).

A small forever-loop worker that watches the Cosmos `system_state` doc
`curator_schedule` and patches the live K8s `CronJob` to match — both
`spec.schedule` (cron) and `spec.suspend` (enabled flag).

Runs as its own Deployment with a 60s poll (cheap; Cosmos read of one
doc + at most one CronJob read). The K8s patch is only sent when the
*desired* (Cosmos) and *live* (K8s) state diverge — so a steady run is
all reads, no writes.

The `kubernetes` client is a lazy import (same pattern as
`backend/services/k8s_jobs.py`) so the AGENTS.md §6 local-dev loop never
pulls it. When `K8S_IN_CLUSTER` is false the worker logs the would-be
patch as ``"reconciler.k8s.skipped_local_dev"`` and skips every K8s API
call — useful when running the reconciler against a local Cosmos
emulator without a cluster.

Never deletes anything (AGENTS.md §5). Patches the schedule field only;
`metadata.annotations[curator.skill-hub/managed-by]` is set to
`reconciler` on first patch so operators can see who owns the spec.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from typing import Any

from backend.core.config import Settings, get_settings
from backend.core.cosmos import (
    SYSTEM_STATE_CONTAINER,
    ensure_containers,
    get_container,
    get_cosmos_client,
)
from backend.core.logging import configure_logging, get_logger
from backend.models.schedule import CuratorSchedule  # noqa: TC001 — used in type hint below
from backend.services import curator_schedule as curator_schedule_svc

log = get_logger(__name__)

# K8s annotation marking the CronJob as managed by this reconciler. Set on
# first patch so a human running `kubectl describe` knows where to change
# the schedule.
MANAGED_BY_ANNOTATION = "curator.skill-hub/managed-by"
MANAGED_BY_VALUE = "reconciler"

DEFAULT_POLL_SECONDS = 60


@dataclass(frozen=True)
class ReconcileDecision:
    """Pure result of comparing desired vs live CronJob state.

    `patch_body` is the JSON-merge-patch dict to send to the K8s API, or
    ``None`` when no change is required (no-op). Surfacing the decision
    explicitly (rather than letting the reconciler call the K8s client
    inline) keeps the diff logic unit-testable without any kubernetes
    fake.
    """

    needs_patch: bool
    patch_body: dict[str, Any] | None
    reason: str


def _compute_decision(
    *,
    desired: CuratorSchedule,
    live_schedule: str | None,
    live_suspend: bool | None,
    live_annotations: dict[str, str] | None,
) -> ReconcileDecision:
    """Return the smallest patch that makes the live CronJob match desired."""
    desired_suspend = not desired.enabled
    annotations = live_annotations or {}
    schedule_drift = live_schedule != desired.cron
    suspend_drift = live_suspend != desired_suspend
    annotation_drift = annotations.get(MANAGED_BY_ANNOTATION) != MANAGED_BY_VALUE

    if not (schedule_drift or suspend_drift or annotation_drift):
        return ReconcileDecision(needs_patch=False, patch_body=None, reason="in_sync")

    patch: dict[str, Any] = {"spec": {}}
    if schedule_drift:
        patch["spec"]["schedule"] = desired.cron
    if suspend_drift:
        patch["spec"]["suspend"] = desired_suspend
    if annotation_drift:
        patch["metadata"] = {"annotations": {MANAGED_BY_ANNOTATION: MANAGED_BY_VALUE}}

    reasons = []
    if schedule_drift:
        reasons.append(f"schedule {live_schedule!r}->{desired.cron!r}")
    if suspend_drift:
        reasons.append(f"suspend {live_suspend!r}->{desired_suspend!r}")
    if annotation_drift:
        reasons.append("annotate managed-by")

    return ReconcileDecision(needs_patch=True, patch_body=patch, reason=", ".join(reasons))


def _in_cluster() -> bool:
    """Mirrors the local-dev fallback in §6 (env-driven, not autodetected)."""
    return os.environ.get("K8S_IN_CLUSTER", "").lower() in ("1", "true", "yes")


def _read_cronjob(*, namespace: str, name: str) -> tuple[str | None, bool | None, dict[str, str]]:
    """Return (schedule, suspend, annotations) of the live CronJob, or
    (None, None, {}) if it doesn't exist yet (reconciler will not create
    it — that's Helm's job)."""
    from kubernetes import client as k8s_client  # lazy (AGENTS.md §13)
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    batch = k8s_client.BatchV1Api()
    try:
        cron = batch.read_namespaced_cron_job(name=name, namespace=namespace)
    except Exception as exc:  # noqa: BLE001 — surface as "absent" not crash
        log.warning(
            "reconciler.k8s.read_failed",
            extra={"namespace": namespace, "name": name, "error": str(exc)},
        )
        return None, None, {}

    schedule = getattr(cron.spec, "schedule", None)
    suspend = bool(getattr(cron.spec, "suspend", False) or False)
    annotations = dict((cron.metadata.annotations or {}) if cron.metadata else {})
    return schedule, suspend, annotations


def _patch_cronjob(*, namespace: str, name: str, body: dict[str, Any]) -> None:
    from kubernetes import client as k8s_client  # lazy
    from kubernetes import config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    batch = k8s_client.BatchV1Api()
    batch.patch_namespaced_cron_job(name=name, namespace=namespace, body=body)


async def reconcile_once(
    *,
    settings: Settings,
    system_state: Any,
) -> ReconcileDecision:
    """One reconcile pass: read desired → read live → decide → (maybe) patch.

    Returns the `ReconcileDecision` so tests can assert intent without
    poking the kubernetes fake.
    """
    desired = await curator_schedule_svc.get_schedule(system_state=system_state)

    namespace = settings.k8s_namespace
    cronjob_name = "curator"  # The Helm CronJob is named `curator` (charts/.../templates/curator/cronjob.yaml).

    if not _in_cluster():
        # Local-dev fallback — no K8s API calls. Still compute what we
        # *would* do so operators can eyeball it in logs.
        decision = _compute_decision(
            desired=desired,
            live_schedule=None,
            live_suspend=None,
            live_annotations=None,
        )
        log.info(
            "reconciler.k8s.skipped_local_dev",
            extra={
                "desired_cron": desired.cron,
                "desired_enabled": desired.enabled,
                "would_patch": decision.needs_patch,
                "would_patch_body": decision.patch_body,
                "reason": decision.reason,
            },
        )
        return decision

    live_schedule, live_suspend, live_annotations = _read_cronjob(
        namespace=namespace, name=cronjob_name
    )
    decision = _compute_decision(
        desired=desired,
        live_schedule=live_schedule,
        live_suspend=live_suspend,
        live_annotations=live_annotations,
    )

    if not decision.needs_patch:
        log.debug(
            "reconciler.k8s.in_sync",
            extra={"cron": desired.cron, "enabled": desired.enabled},
        )
        return decision

    log.info(
        "reconciler.k8s.patching",
        extra={
            "namespace": namespace,
            "name": cronjob_name,
            "patch": decision.patch_body,
            "reason": decision.reason,
        },
    )
    # patch_body is non-None when needs_patch is True (set together in
    # _compute_decision). The check below is a hard assertion for mypy/pyright,
    # not a real branch.
    assert decision.patch_body is not None
    _patch_cronjob(namespace=namespace, name=cronjob_name, body=decision.patch_body)
    return decision


async def run_forever(*, poll_seconds: int = DEFAULT_POLL_SECONDS) -> int:
    """Long-running reconciler loop. Each tick is a `reconcile_once` call."""
    settings = get_settings()
    client = get_cosmos_client(settings)
    try:
        db = await ensure_containers(client, settings.cosmos_db_name)
        system_state = get_container(db, SYSTEM_STATE_CONTAINER)
        log.info(
            "reconciler.started",
            extra={"poll_seconds": poll_seconds, "in_cluster": _in_cluster()},
        )
        while True:
            try:
                await reconcile_once(settings=settings, system_state=system_state)
            except Exception as exc:  # noqa: BLE001 — survive single-pass errors
                log.warning("reconciler.tick_failed", extra={"error": str(exc)})
            await asyncio.sleep(poll_seconds)
    finally:
        await client.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="curator_schedule_reconciler")
    p.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
        help="seconds between reconcile passes (default: 60)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="run one reconcile pass and exit (useful for one-shot Jobs)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    async def _once() -> int:
        settings = get_settings()
        client = get_cosmos_client(settings)
        try:
            db = await ensure_containers(client, settings.cosmos_db_name)
            system_state = get_container(db, SYSTEM_STATE_CONTAINER)
            await reconcile_once(settings=settings, system_state=system_state)
        finally:
            await client.close()
        return 0

    if args.once:
        return asyncio.run(_once())
    return asyncio.run(run_forever(poll_seconds=args.poll_seconds))


if __name__ == "__main__":  # pragma: no cover — module entry
    raise SystemExit(main())
