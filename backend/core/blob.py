"""Async Blob client + helpers for published / archive / snapshots containers.

Blob holds immutable artifact bytes ONLY (AGENTS.md §3). Downloads are served
via signed URLs — never proxy bytes through the app tier.

Two authentication modes are supported (see `Settings.use_blob_identity`):

* Connection string / shared key — used against Azurite locally. Signed
  downloads are produced with an account-key SAS.
* Managed Identity / `az login` — used against real Azure Storage. The client
  is constructed from `BLOB_ACCOUNT_URL` + `DefaultAzureCredential`, and
  signed downloads use a short-lived **user-delegation SAS** (no account key
  ever touches the app).

`DefaultAzureCredential` picks up, in order: env vars, workload/managed
identity, Azure CLI (`az login`), Azure PowerShell, Azure Developer CLI, and
the interactive browser. For local dev, `az login` is the intended path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob import (
    BlobSasPermissions,
    generate_blob_sas,
)
from azure.storage.blob.aio import BlobServiceClient

from backend.core.config import Settings


def _build_credential() -> DefaultAzureCredential:
    """Build a DefaultAzureCredential.

    Kept as a separate factory so tests can patch it. The credential is closed
    by the BlobServiceClient when the service client itself is closed (we pass
    it in via the `credential=` kwarg and call `await blob.close()` on
    shutdown), so callers don't need to manage its lifecycle.
    """
    return DefaultAzureCredential()


def get_blob_service(settings: Settings) -> BlobServiceClient:
    """Return an async BlobServiceClient using identity auth when configured.

    When `BLOB_ACCOUNT_URL` is set, authenticate with DefaultAzureCredential
    (picks up `az login` locally and Managed Identity in Azure). Otherwise
    fall back to the connection string (Azurite / local dev).
    """
    if settings.use_blob_identity():
        return BlobServiceClient(
            account_url=settings.blob_account_url,
            credential=_build_credential(),
        )
    return BlobServiceClient.from_connection_string(settings.blob_connection_string)


async def ensure_containers(svc: BlobServiceClient, settings: Settings) -> None:
    """Create published / archive / snapshots / quarantine / curator containers idempotently.

    `quarantine` is the M5 terminal bucket for skills an admin has rejected
    as malicious. See AGENTS.md §5 — it is the ONE container in the system
    where delete-after-N-days is permitted, and it is owned by a dedicated
    janitor (M5-3); the curator and backend MUST NOT delete from it.
    """
    for name in (
        settings.blob_published_container,
        settings.blob_archive_container,
        settings.blob_snapshots_container,
        settings.blob_quarantine_container,
        settings.curator_reports_container,
    ):
        container = svc.get_container_client(name)
        try:
            await container.create_container()
        except Exception:
            # Already exists — fine.
            pass


def published_blob_path(skill_id: str, version: str) -> str:
    return f"{skill_id}/{version}/bundle.tar.gz"


async def put_published(
    svc: BlobServiceClient,
    settings: Settings,
    *,
    skill_id: str,
    version: str,
    data: bytes,
) -> str:
    """Upload a published bundle. Returns the (unsigned) blob URL."""
    container = svc.get_container_client(settings.blob_published_container)
    blob = container.get_blob_client(published_blob_path(skill_id, version))
    await blob.upload_blob(data, overwrite=True)
    return blob.url


def _parse_connection_string(conn_str: str) -> dict[str, str]:
    return {
        part.split("=", 1)[0]: part.split("=", 1)[1] for part in conn_str.split(";") if "=" in part
    }


async def signed_download_url(
    svc: BlobServiceClient,
    settings: Settings,
    *,
    skill_id: str,
    version: str,
    ttl_minutes: int = 15,
) -> str:
    """Generate a short-lived SAS download URL for a published bundle.

    * Identity mode: produces a **user-delegation SAS** signed via the AAD
      identity backing the service client. No account key required.
    * Connection-string mode: produces an account-key SAS (Azurite parity).
    """
    blob_name = published_blob_path(skill_id, version)
    expiry = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
    permission = BlobSasPermissions(read=True)

    if settings.use_blob_identity():
        # User-delegation key: derived from the AAD identity, valid up to 7 days.
        # We request one slightly larger than `expiry` to cover clock skew.
        start = datetime.now(UTC) - timedelta(minutes=5)
        udk = await svc.get_user_delegation_key(
            key_start_time=start,
            key_expiry_time=expiry + timedelta(minutes=5),
        )
        account_name = svc.account_name
        sas = generate_blob_sas(
            account_name=account_name,
            container_name=settings.blob_published_container,
            blob_name=blob_name,
            user_delegation_key=udk,
            permission=permission,
            expiry=expiry,
            start=start,
        )
        parsed = urlparse(settings.blob_account_url.rstrip("/"))
        return (
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}/"
            f"{settings.blob_published_container}/{blob_name}?{sas}"
        )

    parts = _parse_connection_string(settings.blob_connection_string)
    account_name = parts["AccountName"]
    account_key = parts["AccountKey"]
    blob_endpoint = parts.get(
        "BlobEndpoint", f"https://{account_name}.blob.core.windows.net"
    ).rstrip("/")
    sas = generate_blob_sas(
        account_name=account_name,
        account_key=account_key,
        container_name=settings.blob_published_container,
        blob_name=blob_name,
        permission=permission,
        expiry=expiry,
    )
    parsed = urlparse(blob_endpoint)
    return (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}/"
        f"{settings.blob_published_container}/{blob_name}?{sas}"
    )
