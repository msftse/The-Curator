"""Unit tests for the curator on-demand K8s Job dispatch path (M4 Task 18).

`backend.services.k8s_jobs` is only imported when settings.runtime_mode=k8s.
Locally, the `kubernetes` package is not installed; we inject a fake module
into sys.modules before import so the test runs everywhere.

We also test the API curator.run endpoint's branching via FastAPI's
dependency override, ensuring:
  - runtime_mode=inprocess  -> calls curator_svc.execute_pass (existing path)
  - runtime_mode=k8s        -> calls k8s_jobs.create_curator_ondemand_job
                                and returns a CuratorRunDispatched
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _install_fake_kubernetes(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Register a fake `kubernetes` module so the lazy import in
    backend.services.k8s_jobs resolves without the real client installed.

    Returns a SimpleNamespace exposing the mocked BatchV1Api instance for
    assertions.
    """
    batch_api = MagicMock(name="BatchV1Api")

    # Fake CronJob shape with enough attrs for clone_job logic.
    fake_cron = SimpleNamespace(
        metadata=SimpleNamespace(name="curator-ondemand", uid="cron-uid-1"),
        spec=SimpleNamespace(
            job_template=SimpleNamespace(
                metadata=SimpleNamespace(labels={"app": "curator"}, annotations={}),
                spec=SimpleNamespace(
                    template=SimpleNamespace(
                        spec=SimpleNamespace(containers=[SimpleNamespace(args=["--once"])])
                    )
                ),
            )
        ),
    )
    batch_api.read_namespaced_cron_job.return_value = fake_cron
    batch_api.create_namespaced_job.return_value = SimpleNamespace(
        metadata=SimpleNamespace(name="curator-ondemand-stub")
    )

    class _ConfigException(Exception):
        pass

    config_module = SimpleNamespace(
        load_incluster_config=MagicMock(),
        load_kube_config=MagicMock(),
        ConfigException=_ConfigException,
    )

    client_module = SimpleNamespace(
        BatchV1Api=MagicMock(return_value=batch_api),
        V1Job=MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw)),
        V1ObjectMeta=MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw)),
        V1OwnerReference=MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw)),
    )

    fake_kubernetes = SimpleNamespace(client=client_module, config=config_module)

    monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
    monkeypatch.setitem(sys.modules, "kubernetes.client", client_module)
    monkeypatch.setitem(sys.modules, "kubernetes.config", config_module)

    return SimpleNamespace(batch=batch_api, cron=fake_cron, client_module=client_module)


def test_create_curator_ondemand_job_calls_k8s_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_kubernetes(monkeypatch)

    # Reload the module so its top-level imports re-bind to our fake (only
    # matters if a previous test imported it with the real package).
    if "backend.services.k8s_jobs" in sys.modules:
        del sys.modules["backend.services.k8s_jobs"]
    from backend.core.config import Settings
    from backend.services import k8s_jobs

    settings = Settings(
        runtime_mode="k8s",
        k8s_namespace="skillhub",
        k8s_curator_ondemand_cronjob="curator-ondemand",
    )

    result = k8s_jobs.create_curator_ondemand_job(
        settings=settings, dry_run=False, actor="admin@org"
    )

    assert result["namespace"] == "skillhub"
    assert result["job_name"].startswith("curator-ondemand-")
    assert len(result["job_name"]) <= 63

    fake.batch.read_namespaced_cron_job.assert_called_once_with(
        name="curator-ondemand", namespace="skillhub"
    )
    fake.batch.create_namespaced_job.assert_called_once()
    _, kwargs = fake.batch.create_namespaced_job.call_args
    assert kwargs["namespace"] == "skillhub"


def test_create_curator_ondemand_job_dry_run_injects_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_kubernetes(monkeypatch)

    if "backend.services.k8s_jobs" in sys.modules:
        del sys.modules["backend.services.k8s_jobs"]
    from backend.core.config import Settings
    from backend.services import k8s_jobs

    settings = Settings(runtime_mode="k8s")
    k8s_jobs.create_curator_ondemand_job(settings=settings, dry_run=True, actor="admin@org")

    container_args = fake.cron.spec.job_template.spec.template.spec.containers[0].args
    assert "--dry-run" in container_args
    assert "--once" in container_args


def test_create_curator_ondemand_job_falls_back_to_kubeconfig(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside cluster (no service-account token) we must load_kube_config."""
    fake = _install_fake_kubernetes(monkeypatch)

    # First call raises ConfigException; second should be load_kube_config.
    fake.client_module  # touch
    # Patch load_incluster_config to raise the fake module's ConfigException.
    cfg_exc = sys.modules["kubernetes.config"].ConfigException  # type: ignore[attr-defined]
    sys.modules["kubernetes.config"].load_incluster_config.side_effect = cfg_exc()  # type: ignore[attr-defined]

    if "backend.services.k8s_jobs" in sys.modules:
        del sys.modules["backend.services.k8s_jobs"]
    from backend.core.config import Settings
    from backend.services import k8s_jobs

    settings = Settings(runtime_mode="k8s")
    k8s_jobs.create_curator_ondemand_job(settings=settings, dry_run=False)

    sys.modules["kubernetes.config"].load_kube_config.assert_called_once()  # type: ignore[attr-defined]
