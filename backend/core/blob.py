"""Async Blob client + helpers for published / archive / snapshots containers.

Blob holds immutable artifact bytes ONLY (AGENTS.md §3). Downloads are served
via signed URLs — never proxy bytes through the app tier.

Against Azurite we use an account-key SAS. M1 will swap to user-delegation SAS
when we move to real Storage with managed identity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from azure.storage.blob import BlobSasPermissions, generate_blob_sas
from azure.storage.blob.aio import BlobServiceClient

from backend.core.config import Settings


def get_blob_service(settings: Settings) -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(settings.blob_connection_string)


async def ensure_containers(svc: BlobServiceClient, settings: Settings) -> None:
    """Create published / archive / snapshots / curator containers idempotently."""
    for name in (
        settings.blob_published_container,
        settings.blob_archive_container,
        settings.blob_snapshots_container,
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


def signed_download_url(
    settings: Settings,
    *,
    skill_id: str,
    version: str,
    ttl_minutes: int = 15,
) -> str:
    """Generate an account-key SAS URL for a published bundle.

    Works against Azurite the same as prod Storage. M1 should switch to
    user-delegation SAS once we have managed identity in Azure.
    """
    parts = _parse_connection_string(settings.blob_connection_string)
    account_name = parts["AccountName"]
    account_key = parts["AccountKey"]
    blob_endpoint = parts.get(
        "BlobEndpoint", f"https://{account_name}.blob.core.windows.net"
    ).rstrip("/")

    blob_name = published_blob_path(skill_id, version)
    sas = generate_blob_sas(
        account_name=account_name,
        account_key=account_key,
        container_name=settings.blob_published_container,
        blob_name=blob_name,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
    )
    # Build URL preserving the Azurite path-style endpoint.
    parsed = urlparse(blob_endpoint)
    return (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}/"
        f"{settings.blob_published_container}/{blob_name}?{sas}"
    )
