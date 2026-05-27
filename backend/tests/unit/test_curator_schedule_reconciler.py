"""Unit tests for the curator schedule reconciler (M5-7).

The reconciler's K8s touchpoint is gated behind `K8S_IN_CLUSTER` — outside
the cluster it logs the would-be patch and skips every K8s call. This
suite exercises:

  - `_compute_decision` — pure diff function (no K8s, no I/O).
  - `reconcile_once` — local-dev path (no patches sent) + in-cluster
    path with a fake `kubernetes` module patched into `sys.modules`.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.core.config import Settings
from backend.workers import curator_schedule_reconciler as reconciler


# ---- _compute_decision (pure) ---------------------------------------


def _desired(cron: str = "0 3 * * 0", enabled: bool = True) -> reconciler.CuratorSchedule:
    return reconciler.CuratorSchedule(cron=cron, timezone="UTC", enabled=enabled)


def test_decision_in_sync_is_noop() -> None:
    decision = reconciler._compute_decision(
        desired=_desired(),
        live_schedule="0 3 * * 0",
        live_suspend=False,
        live_annotations={reconciler.MANAGED_BY_ANNOTATION: reconciler.MANAGED_BY_VALUE},
    )
    assert decision.needs_patch is False
    assert decision.patch_body is None


def test_decision_schedule_drift_emits_patch() -> None:
    decision = reconciler._compute_decision(
        desired=_desired(cron="0 4 * * 0"),
        live_schedule="0 3 * * 0",
        live_suspend=False,
        live_annotations={reconciler.MANAGED_BY_ANNOTATION: reconciler.MANAGED_BY_VALUE},
    )
    assert decision.needs_patch is True
    assert decision.patch_body == {"spec": {"schedule": "0 4 * * 0"}}


def test_decision_suspend_drift_emits_patch() -> None:
    decision = reconciler._compute_decision(
        desired=_desired(enabled=False),
        live_schedule="0 3 * * 0",
        live_suspend=False,
        live_annotations={reconciler.MANAGED_BY_ANNOTATION: reconciler.MANAGED_BY_VALUE},
    )
    assert decision.needs_patch is True
    assert decision.patch_body == {"spec": {"suspend": True}}


def test_decision_missing_annotation_emits_patch() -> None:
    decision = reconciler._compute_decision(
        desired=_desired(),
        live_schedule="0 3 * * 0",
        live_suspend=False,
        live_annotations={},
    )
    assert decision.needs_patch is True
    assert decision.patch_body is not None
    assert "metadata" in decision.patch_body
    assert (
        decision.patch_body["metadata"]["annotations"][reconciler.MANAGED_BY_ANNOTATION]
        == reconciler.MANAGED_BY_VALUE
    )


def test_decision_combined_drift() -> None:
    decision = reconciler._compute_decision(
        desired=_desired(cron="*/15 * * * *", enabled=False),
        live_schedule="0 3 * * 0",
        live_suspend=False,
        live_annotations={},
    )
    assert decision.needs_patch is True
    body = decision.patch_body
    assert body is not None
    assert body["spec"]["schedule"] == "*/15 * * * *"
    assert body["spec"]["suspend"] is True
    assert reconciler.MANAGED_BY_ANNOTATION in body["metadata"]["annotations"]


# ---- reconcile_once local-dev fallback ------------------------------


class _FakeSystemState:
    def __init__(self, doc: dict[str, Any] | None = None) -> None:
        self._doc = doc

    async def read_item(self, *, item: str, partition_key: str) -> dict[str, Any]:  # noqa: ARG002
        if self._doc is None:
            from azure.cosmos import exceptions as cosmos_exc

            raise cosmos_exc.CosmosResourceNotFoundError(
                status_code=404, message="not found"
            )
        return self._doc


async def test_reconcile_once_skips_when_not_in_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("K8S_IN_CLUSTER", raising=False)
    settings = Settings()  # type: ignore[call-arg]
    state = _FakeSystemState()  # no doc -> default schedule

    # If the lazy `kubernetes` import were attempted we'd blow up — the
    # package isn't installed in local-dev. Reaching the end without
    # raising is itself the assertion.
    decision = await reconciler.reconcile_once(settings=settings, system_state=state)
    # Default desired vs empty live → wants to patch (annotation + schedule).
    assert decision.needs_patch is True


# ---- reconcile_once in-cluster path with fake k8s -------------------


def _install_fake_kubernetes(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    batch_api = MagicMock(name="BatchV1Api")

    fake_cron = SimpleNamespace(
        metadata=SimpleNamespace(annotations={}, name="curator"),
        spec=SimpleNamespace(schedule="0 3 * * 0", suspend=False),
    )
    batch_api.read_namespaced_cron_job.return_value = fake_cron
    batch_api.patch_namespaced_cron_job.return_value = fake_cron

    class _ConfigException(Exception):
        pass

    config_module = SimpleNamespace(
        load_incluster_config=MagicMock(),
        load_kube_config=MagicMock(),
        ConfigException=_ConfigException,
    )
    client_module = SimpleNamespace(BatchV1Api=MagicMock(return_value=batch_api))
    fake_kubernetes = SimpleNamespace(client=client_module, config=config_module)

    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.client", client_module)
    monkeypatch.setitem(sys.modules, "kubernetes.config", config_module)

    return SimpleNamespace(batch=batch_api, cron=fake_cron)


async def test_reconcile_once_in_cluster_patches_when_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("K8S_IN_CLUSTER", "1")
    fake = _install_fake_kubernetes(monkeypatch)
    settings = Settings()  # type: ignore[call-arg]

    # Cosmos doc asks for a different schedule than the live CronJob.
    state = _FakeSystemState(
        doc={
            "id": "curator_schedule",
            "key": "curator_schedule",
            "cron": "0 4 * * 0",
            "timezone": "UTC",
            "enabled": True,
            "updated_by": "alice@org",
            "updated_at": "2026-05-21T11:00:00+00:00",
        }
    )

    decision = await reconciler.reconcile_once(settings=settings, system_state=state)
    assert decision.needs_patch is True
    fake.batch.read_namespaced_cron_job.assert_called_once_with(
        name="curator", namespace=settings.k8s_namespace
    )
    fake.batch.patch_namespaced_cron_job.assert_called_once()
    _, kwargs = fake.batch.patch_namespaced_cron_job.call_args
    assert kwargs["namespace"] == settings.k8s_namespace
    assert kwargs["name"] == "curator"
    assert kwargs["body"]["spec"]["schedule"] == "0 4 * * 0"


async def test_reconcile_once_in_cluster_is_noop_when_in_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("K8S_IN_CLUSTER", "1")
    fake = _install_fake_kubernetes(monkeypatch)
    # Stamp the annotation + matching schedule.
    fake.cron.metadata.annotations[reconciler.MANAGED_BY_ANNOTATION] = (
        reconciler.MANAGED_BY_VALUE
    )
    fake.cron.spec.schedule = "0 3 * * 0"
    fake.cron.spec.suspend = False
    settings = Settings()  # type: ignore[call-arg]
    state = _FakeSystemState()  # default → 0 3 * * 0, enabled

    decision = await reconciler.reconcile_once(settings=settings, system_state=state)
    assert decision.needs_patch is False
    fake.batch.patch_namespaced_cron_job.assert_not_called()


# ---- CLI argparse ----------------------------------------------------


def test_parse_args_defaults() -> None:
    args = reconciler._parse_args([])
    assert args.once is False
    assert args.poll_seconds == reconciler.DEFAULT_POLL_SECONDS


def test_parse_args_once_and_poll() -> None:
    args = reconciler._parse_args(["--once", "--poll-seconds", "5"])
    assert args.once is True
    assert args.poll_seconds == 5
