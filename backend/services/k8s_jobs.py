"""Kubernetes Job creation for the curator on-demand path (M4).

Only imported when `settings.runtime_mode == "k8s"`. Local-dev imports must
not pull this module — the `kubernetes` client is an optional dependency
and isn't installed in `uv sync` without `--extra k8s`.

Contract: clone the podTemplate of a suspended CronJob into a one-shot Job
named `{cronjob}-{utc-iso}-{nonce}`. Returns the created job name.

This is the ONLY place in the codebase that touches the K8s API. RBAC for
the backend ServiceAccount is scoped accordingly:

  - get/list cronjobs.batch in `skillhub`
  - create/get/list/watch jobs.batch in `skillhub`

See charts/agentic-skill-hub/templates/backend/role.yaml.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from backend.core.config import Settings
from backend.core.logging import get_logger

log = get_logger(__name__)


def _job_name(cronjob_name: str) -> str:
    """`curator-ondemand-20260516T120000-3b7e` — 63-char K8s name limit safe."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    nonce = secrets.token_hex(2)
    base = f"{cronjob_name}-{ts}-{nonce}"
    return base[:63]


def create_curator_ondemand_job(
    *,
    settings: Settings,
    dry_run: bool = False,
    actor: str | None = None,
) -> dict[str, str]:
    """Create a one-shot Job cloned from the curator-ondemand CronJob.

    Synchronous on purpose — the kubernetes client is sync, and the call is
    short (a single POST to the API server). FastAPI handlers can await
    `asyncio.to_thread(create_curator_ondemand_job, ...)` if they want to
    avoid blocking the event loop, but the call is fast enough that we
    invoke it directly today.

    Returns: {"job_name": "...", "namespace": "..."}.
    """
    # Lazy import — kubernetes is an optional extra (see pyproject.toml [k8s]).
    # Local-dev installs do not include it; this module is only imported
    # when settings.runtime_mode == "k8s", gated upstream in api/curator.py.
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config

    try:
        # In-cluster config: ServiceAccount token mounted at
        # /var/run/secrets/kubernetes.io/serviceaccount/token.
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        # Outside cluster (manual smoke test from a dev laptop):
        # load ~/.kube/config. Never reached in prod pods.
        k8s_config.load_kube_config()

    batch = k8s_client.BatchV1Api()
    namespace = settings.k8s_namespace
    cronjob_name = settings.k8s_curator_ondemand_cronjob

    cron = batch.read_namespaced_cron_job(name=cronjob_name, namespace=namespace)
    job_template = cron.spec.job_template

    job_name = _job_name(cronjob_name)

    # Build the args. The template CronJob's default container args are
    # `["--once"]`; for a dry-run admin invocation we override.
    if dry_run:
        # Mutate a copy so we don't disturb the cached CronJob object.
        for c in job_template.spec.template.spec.containers:
            if c.args is None:
                c.args = []
            if "--dry-run" not in c.args:
                c.args = list(c.args) + ["--dry-run"]

    labels = dict(job_template.metadata.labels or {})
    labels["app.kubernetes.io/component"] = "curator"
    labels["skillhub.invocation"] = "ondemand"
    annotations = dict(job_template.metadata.annotations or {})
    if actor:
        # Free-form audit breadcrumb; visible in `kubectl describe job`.
        annotations["skillhub.actor"] = actor
    annotations["skillhub.dry-run"] = "true" if dry_run else "false"

    body = k8s_client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=k8s_client.V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels=labels,
            annotations=annotations,
            owner_references=[
                k8s_client.V1OwnerReference(
                    api_version="batch/v1",
                    kind="CronJob",
                    name=cron.metadata.name,
                    uid=cron.metadata.uid,
                    block_owner_deletion=False,
                    controller=False,
                )
            ],
        ),
        spec=job_template.spec,
    )

    batch.create_namespaced_job(namespace=namespace, body=body)
    log.info(
        "k8s_curator_job_created",
        extra={
            "job_name": job_name,
            "namespace": namespace,
            "actor": actor,
            "dry_run": dry_run,
        },
    )
    return {"job_name": job_name, "namespace": namespace}
